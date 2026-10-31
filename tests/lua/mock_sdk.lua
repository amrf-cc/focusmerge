--[[ Minimal Lightroom SDK stand-in for tests: cooperative tasks, real file system, real
     subprocesses (so the real engine runs). Configure through the global MOCK table. ]]
local M = { log = { dialogs = {}, added = {}, captions = {}, bezels = {}, fractions = {} } }
local tasks = {}

local function q(s) return "'" .. (tostring(s):gsub("'", "'\\''")) .. "'" end

local function spawn(fn)
  tasks[#tasks + 1] = coroutine.create(fn)
end

function M.runAll()
  while #tasks > 0 do
    for i = #tasks, 1, -1 do
      local co = tasks[i]
      local ok, err = coroutine.resume(co)
      if not ok then error(err, 0) end
      if coroutine.status(co) == 'dead' then table.remove(tasks, i) end
    end
  end
end

local LrTasks = {}
function LrTasks.startAsyncTask(fn) spawn(fn) end
function LrTasks.sleep(sec)
  os.execute('sleep ' .. math.min(sec, 0.05))
  coroutine.yield()
end
-- Lua 5.1's pcall cannot yield; Lightroom provides LrTasks.pcall for tasks. Same contract here:
-- run f in its own coroutine and re-yield to the scheduler whenever it yields.
function LrTasks.pcall(f, ...)
  local co = coroutine.create(f)
  local r = { coroutine.resume(co, ...) }
  while coroutine.status(co) ~= 'dead' do
    coroutine.yield()
    r = { coroutine.resume(co) }
  end
  return unpack(r)
end
function LrTasks.execute(cmd)
  local exitFile = os.tmpname()
  os.remove(exitFile)
  os.execute('( ' .. cmd .. ' ; echo $? > ' .. q(exitFile) .. ' ) >/dev/null 2>&1 &')
  while true do
    local f = io.open(exitFile, 'r')
    if f then
      local content = f:read('*a')
      f:close()
      if content ~= '' then os.remove(exitFile); return tonumber(content) end
    end
    os.execute('sleep 0.02')
    coroutine.yield()
  end
end

local LrFileUtils = {}
function LrFileUtils.exists(p) return os.rename(p, p) and true or false end
function LrFileUtils.createAllDirectories(p) os.execute('mkdir -p ' .. q(p)); return true end
function LrFileUtils.readFile(p)
  local f = io.open(p, 'r')
  if not f then return nil end
  local s = f:read('*a'); f:close(); return s
end
function LrFileUtils.delete(p) os.execute('rm -rf ' .. q(p)); return true end

local LrPathUtils = {}
function LrPathUtils.leafName(p) return p:match('([^/]*)$') end
function LrPathUtils.parent(p) return p:match('^(.*)/[^/]*$') or '/' end
function LrPathUtils.child(p, c) return p .. '/' .. c end
function LrPathUtils.getStandardFilePath(which)
  if which == 'home' then return MOCK.home end
  return MOCK.tmp
end

local LrDialogs = {}
function LrDialogs.message(title, info, style)
  M.log.dialogs[#M.log.dialogs + 1] = { title = title, info = info, style = style }
end
function LrDialogs.showBezel(msg) M.log.bezels[#M.log.bezels + 1] = msg end

local LrDate = { currentTime = function() return os.time() end }

local LrExportSession = function(params)
  if MOCK.throwOnSession then error('boom: export session could not be created') end
  local session = {}
  function session:renditions(opts)
    local i = 0
    return function()
      i = i + 1
      local photo = params.photosToExport[i]
      if not photo then return nil end
      local dest = params.exportSettings.LR_export_destinationPathPrefix .. '/' .. LrPathUtils.leafName(photo.path)
      local index = i
      return i, {
        photo = photo,
        destinationPath = dest,
        waitForRender = function()
          os.execute('sleep ' .. tostring(MOCK.renderDelay or 0.05))   -- "rendering" takes real time
          coroutine.yield()
          if MOCK.failRenderAt == index then return false, 'mock render failure' end
          os.execute('cp ' .. q(photo.path) .. ' ' .. q(dest))
          M.log.exported = (M.log.exported or 0) + 1
          return true, dest
        end,
      }
    end
  end
  return session
end

local LrProgressScope = function(params)
  local scope = {}
  function scope:setCancelable() end
  function scope:setPortionComplete(done, total) M.log.fractions[#M.log.fractions + 1] = { done, total } end
  function scope:setCaption(c) M.log.captions[#M.log.captions + 1] = c end
  function scope:isCanceled()
    return MOCK.cancelAfterExports ~= nil and (M.log.exported or 0) >= MOCK.cancelAfterExports
  end
  function scope:done() M.log.progressDone = true end
  function scope:cancel() MOCK.cancelAfterExports = 0 end
  return scope
end

local function makePhoto(spec)
  local photo = { path = spec.path }
  function photo:getRawMetadata(key)
    if key == 'path' then return self.path end
    if key == 'dateTimeOriginal' then return spec.time end
    if key == 'isVideo' then return false end
    if key == 'isInStackInFolder' then return not MOCK.notStacked end
    if key == 'croppedDimensions' then return { width = spec.width or 6240, height = spec.height or 4160 } end
    return nil
  end
  return photo
end

local catalog = {}
function catalog:getTargetPhotos()
  local out = {}
  for i, spec in ipairs(MOCK.photos) do out[i] = makePhoto(spec) end
  M.photos = out
  return out
end
function catalog:withWriteAccessDo(name, fn, params)
  fn()
  return 'executed'
end
function catalog:addPhoto(path, stackWith, position)
  if MOCK.addPhotoThrows then error('catalog busy') end
  M.log.added[#M.log.added + 1] = { path = path, stackWith = stackWith, position = position }
  return makePhoto({ path = path })
end
function catalog:setSelectedPhotos(active, others) M.log.selected = active end

M.env = {
  LrApplication = { activeCatalog = function() return catalog end },
  LrTasks = LrTasks, LrFileUtils = LrFileUtils, LrPathUtils = LrPathUtils, LrDialogs = LrDialogs,
  LrExportSession = LrExportSession, LrProgressScope = LrProgressScope, LrDate = LrDate,
}
M.spawn = spawn
return M
