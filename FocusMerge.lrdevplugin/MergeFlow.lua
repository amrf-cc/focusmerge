--[[ The Focus Merge flow. SDK handles come in through `env` so the same code runs inside
     Lightroom and against tests/lua/mock_sdk.lua. Must run inside an async task. ]]
local MergeFlow = {}

local ENGINE_FINISH_SECONDS = 600   -- bound on waiting for the engine after the last frame / abort

local function appendLine(path, line)
  local f = io.open(path, 'a')
  if not f then return false end
  local ok = f:write(line, '\n')
  f:close()
  return ok ~= nil
end

local function touch(path)
  local f = io.open(path, 'w')
  if not f then return false end
  f:close()
  return true
end

local function execute(env)
  local Common, LrTasks, LrFileUtils, LrPathUtils, LrDialogs, LrDate =
    env.Common, env.LrTasks, env.LrFileUtils, env.LrPathUtils, env.LrDialogs, env.LrDate
  local prefs = Common.withDefaults(env.prefs)
  local catalog = env.LrApplication.activeCatalog()

  local function join(base, ...)
    local p = base
    for _, part in ipairs({ ... }) do p = LrPathUtils.child(p, part) end
    return p
  end
  local function say(title, info, style)
    LrDialogs.message(title, info, style or 'info')
  end

  -- 1. Selection ---------------------------------------------------------------------------
  local photos = catalog:getTargetPhotos()
  if #photos < 2 then
    say('Focus Merge', 'Select two or more photos from one focus stack.')
    return
  end
  local entries = {}
  for _, photo in ipairs(photos) do
    if photo:getRawMetadata('isVideo') then
      say('Focus Merge', 'The selection contains a video, which cannot be focus merged.')
      return
    end
    local path = photo:getRawMetadata('path')
    entries[#entries + 1] = {
      photo = photo, path = path, name = LrPathUtils.leafName(path),
      time = photo:getRawMetadata('dateTimeOriginal'),
    }
  end
  Common.sortEntries(entries)
  local sorted = {}
  for i, e in ipairs(entries) do sorted[i] = e.photo end
  local count, first = #entries, entries[1]

  -- 2. Engine ------------------------------------------------------------------------------
  local home = LrPathUtils.getStandardFilePath('home')
  local python = prefs.pythonPath
  if python == '' then
    python = join(home, 'Library', 'Application Support', 'FocusMerge', 'venv', 'bin', 'python')
  end
  local enginePath = join(env.pluginPath, 'engine')
  if not LrFileUtils.exists(python) or not LrFileUtils.exists(enginePath) then
    say('Focus Merge is not installed',
      'The merge engine was not found:\n' .. python ..
      '\n\nRun install.sh from the FocusMerge folder in Terminal, then try again.', 'critical')
    return
  end

  -- 3. Folders: checked before anything is exported or started ------------------------------
  for _, setting in ipairs({ { 'work folder', prefs.workFolder }, { 'output folder', prefs.outputFolder } }) do
    if setting[2] ~= '' and not Common.isAbsolute(setting[2]) then
      say('Focus Merge', 'The ' .. setting[1] .. ' must be an absolute path (starting with /):\n' .. setting[2], 'critical')
      return
    end
  end
  local outDir = prefs.outputFolder
  if outDir == '' then
    outDir = LrPathUtils.parent(first.path)
  else
    LrFileUtils.createAllDirectories(outDir)
  end
  local probe = join(outDir, '.focusmerge-write-test')
  if not touch(probe) then
    say('Focus Merge', 'Cannot write to the output folder:\n' .. outDir, 'critical')
    return
  end
  LrFileUtils.delete(probe)

  local base = prefs.workFolder
  if base == '' then base = LrPathUtils.getStandardFilePath('temp') end
  math.randomseed(math.floor(LrDate.currentTime()))
  local workDir = join(base, 'focusmerge-' .. string.format('%d-%04d', math.floor(LrDate.currentTime()),
    math.random(0, 9999)))
  LrFileUtils.createAllDirectories(workDir)
  local files = {
    queue = join(workDir, 'queue.txt'), progress = join(workDir, 'progress.txt'),
    result = join(workDir, 'result.json'), cancel = join(workDir, 'cancel'),
    error = join(workDir, 'error.txt'),
  }
  if not LrFileUtils.exists(workDir) or not touch(files.queue) then
    say('Focus Merge', 'Cannot create the work folder:\n' .. workDir ..
      '\n\nChange the work folder in Focus Merge Settings (or leave it empty).', 'critical')
    return
  end
  local logDir = join(home, 'Library', 'Logs', 'FocusMerge')
  LrFileUtils.createAllDirectories(logDir)
  local logFile = join(logDir, 'last-run.log')

  local function cleanup()
    local listed = LrFileUtils.readFile(files.queue)
    if listed then   -- remove each frame we handed over, then the folder
      for line in listed:gmatch('[^\r\n]+') do
        if line:sub(1, #workDir + 1) == workDir .. '/' then LrFileUtils.delete(line) end
      end
    end
    for _, f in pairs(files) do LrFileUtils.delete(f) end
    if LrPathUtils.leafName(workDir):find('^focusmerge%-') then LrFileUtils.delete(workDir) end
  end

  local outName = Common.uniqueName(Common.outputName(first.name, count), function(candidate)
    return LrFileUtils.exists(join(outDir, candidate))
  end)
  local outPath = join(outDir, outName)

  local longEdge = nil
  if prefs.sizePercent < 100 then
    local dims = first.photo:getRawMetadata('croppedDimensions')
    if dims then longEdge = Common.longEdgePixels(dims.width, dims.height, prefs.sizePercent) end
  end
  local exportSettings = Common.buildExportSettings({
    destination = workDir, colorSpace = prefs.colorSpace, longEdgePixels = longEdge,
  })

  -- 4. Start the engine (own task: execute() blocks it) -----------------------------------
  local cmd = Common.buildCommand({
    python = python, enginePath = enginePath, framesFile = files.queue, out = outPath,
    progress = files.progress, result = files.result, cancel = files.cancel, error = files.error,
    expected = count, align = prefs.align, sharpness = prefs.sharpness, log = logFile,
  })
  local state = { engineDone = false, engineExit = nil }
  LrTasks.startAsyncTask(function()
    state.engineExit = LrTasks.execute(cmd)
    state.engineDone = true
  end)

  -- 5. Progress UI + cancel ----------------------------------------------------------------
  local progress = env.LrProgressScope({ title = 'Focus Merge', functionContext = env.context })
  progress:setCancelable(true)
  local canceled = false
  local function requestCancel()
    if not canceled then
      canceled = true
      touch(files.cancel)
      appendLine(files.queue, '#ABORT')
    end
  end
  LrTasks.startAsyncTask(function()
    while not state.engineDone do
      if LrFileUtils.exists(files.progress) then
        local p = Common.parseProgress(LrFileUtils.readFile(files.progress))
        if p then
          progress:setCaption(string.format('Merging frame %d of %d', p.done, p.total or count))
        end
      end
      if progress:isCanceled() then requestCancel() end
      LrTasks.sleep(0.25)
    end
  end)

  -- 6. Export, feeding the engine as frames render -----------------------------------------
  local failure, internalError = nil, nil
  local fedOk, fedErr = LrTasks.pcall(function()
    local session = env.LrExportSession({ photosToExport = sorted, exportSettings = exportSettings })
    for _, rendition in session:renditions({ progressScope = progress, stopIfCanceled = true }) do
      if state.engineDone then break end            -- the engine died: stop rendering for nothing
      local ok, pathOrMessage = rendition:waitForRender()
      if not ok then
        local named, path = LrTasks.pcall(function() return rendition.photo:getRawMetadata('path') end)
        local who = (named and path) and LrPathUtils.leafName(path) or 'a frame'
        failure = who .. ': ' .. tostring(pathOrMessage)
        break
      end
      if not appendLine(files.queue, pathOrMessage) then
        failure = 'cannot write to the work folder (' .. files.queue .. ')'
        break
      end
      if progress:isCanceled() then
        requestCancel()
        break
      end
    end
  end)
  if not fedOk then
    internalError = fedErr
    failure = failure or ('unexpected error: ' .. tostring(fedErr))
  end
  if failure or canceled then
    requestCancel()
    if progress.cancel then LrTasks.pcall(progress.cancel, progress) end   -- stop pending renders
  elseif not state.engineDone then
    appendLine(files.queue, '#END')
  end

  -- 7. Wait for the engine, bounded --------------------------------------------------------
  local deadline = LrDate.currentTime() + ENGINE_FINISH_SECONDS
  while not state.engineDone and LrDate.currentTime() < deadline do LrTasks.sleep(0.1) end
  progress:done()
  local exitCode = Common.normalizeExit(state.engineExit)

  -- 8. Import the result -------------------------------------------------------------------
  if not failure and not canceled and state.engineDone and exitCode == 0 and LrFileUtils.exists(outPath) then
    local stackWith = nil
    if outDir == LrPathUtils.parent(first.path) then stackWith = sorted[1] end   -- stacks are per folder
    local imported = nil
    local ok, err = LrTasks.pcall(function()
      catalog:withWriteAccessDo('Focus Merge: add result', function()
        imported = catalog:addPhoto(outPath, stackWith, 'above')
      end, { timeout = 30 })
      if imported then catalog:setSelectedPhotos(imported, {}) end
    end)
    if not ok or not imported then
      say('Focus Merge: merged, but not added to the catalog',
        'The merged image was saved as:\n' .. outPath .. '\n\nIt could not be added to the catalog (' ..
        tostring(err or 'the catalog was busy') .. '). Add it with File > Add Photos to Catalog.', 'warning')
    else
      local notes = {}
      local result = Common.parseResult(LrFileUtils.readFile(files.result))
      if result then
        for _, w in ipairs(result.warnings) do notes[#notes + 1] = w end
      end
      if stackWith then
        local okS, inStack = LrTasks.pcall(function() return imported:getRawMetadata('isInStackInFolder') end)
        if okS and inStack == false then
          notes[#notes + 1] = 'The merged photo could not be stacked with the originals. ' ..
            'Select the originals and the result and press ⌘G.'
        end
      end
      if #notes > 0 then
        say('Focus Merge finished with notes', table.concat(notes, '\n'), 'warning')
      else
        LrDialogs.showBezel('Focus Merge: ' .. tostring(count) .. ' frames merged')
      end
    end
  elseif failure then
    if not internalError then
      say('Focus Merge failed', 'A frame could not be processed:\n' .. failure, 'critical')
    end
  elseif not canceled then
    local reason = Common.firstLine(LrFileUtils.readFile(files.error))
    if not state.engineDone then
      reason = 'The merge engine did not finish in time.'
    elseif not reason then
      reason = 'The merge engine stopped with exit code ' .. tostring(exitCode) .. '.'
    end
    say('Focus Merge failed', reason .. '\n\nEngine log: ' .. logFile, 'critical')
  end

  -- 9. Clean up ----------------------------------------------------------------------------
  cleanup()
  if internalError then error(internalError, 0) end
end

function MergeFlow.run(env)
  if MergeFlow.busy then
    env.LrDialogs.message('Focus Merge', 'A merge is already running. Wait for it to finish.', 'info')
    return
  end
  MergeFlow.busy = true
  local ok, err = env.LrTasks.pcall(execute, env)
  MergeFlow.busy = false
  if not ok then error(err, 0) end
end

return MergeFlow
