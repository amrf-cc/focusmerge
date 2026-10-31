--[[ Pure helpers: no Lightroom SDK access, so they can be unit-tested outside Lightroom.
     Lua 5.1 compatible. ]]
local Common = {}

Common.PREF_DEFAULTS = {
  sizePercent = 100,
  colorSpace = 'ProPhotoRGB',
  sharpness = 2,
  align = 'auto',
  workFolder = '',
  outputFolder = '',
  pythonPath = '',
}

function Common.withDefaults(prefs)
  local out = {}
  for key, default in pairs(Common.PREF_DEFAULTS) do
    local value = prefs and prefs[key]
    if value == nil then out[key] = default else out[key] = value end
  end
  return out
end

-- POSIX single-quote quoting. A nil is a bug upstream (it used to become the file name 'nil').
function Common.shellQuote(s)
  if s == nil then error('shellQuote: value is nil', 2) end
  return "'" .. (tostring(s):gsub("'", "'\\''")) .. "'"
end

function Common.stem(name)
  return (name:gsub('%.[^%.]*$', ''))
end

function Common.outputName(firstFileName, count)
  return Common.stem(firstFileName) .. '_FM' .. tostring(count) .. '.tif'
end

-- existsFn(candidateName) -> boolean
function Common.uniqueName(name, existsFn)
  if not existsFn(name) then return name end
  local stem, ext = name:match('^(.*)(%.[^%.]*)$')
  if not stem then stem, ext = name, '' end
  local i = 1
  while existsFn(stem .. '-' .. i .. ext) do i = i + 1 end
  return stem .. '-' .. i .. ext
end

function Common.sortEntries(entries)
  table.sort(entries, function(a, b)
    local ta, tb = a.time or 0, b.time or 0
    if ta ~= tb then return ta < tb end
    return string.lower(a.name) < string.lower(b.name)
  end)
  return entries
end

function Common.longEdgePixels(width, height, percent)
  return math.max(1, math.floor(math.max(width, height) * percent / 100 + 0.5))
end

function Common.buildExportSettings(opts)
  local s = {
    LR_format = 'TIFF',
    LR_export_bitDepth = 16,
    LR_export_colorSpace = opts.colorSpace or 'ProPhotoRGB',
    LR_tiff_compressionMethod = 'compressionMethod_None',
    LR_tiff_preserveTransparency = false,
    LR_outputSharpeningOn = false,
    LR_embeddedMetadataOption = 'all',
    LR_export_destinationType = 'specificFolder',
    LR_export_destinationPathPrefix = opts.destination,
    LR_export_useSubfolder = false,
    LR_export_useParentFolder = false,
    LR_collisionHandling = 'rename',
    LR_renamingTokensOn = false,
    LR_extensionCase = 'lowercase',
    LR_reimportExportedPhoto = false,
    LR_useWatermark = false,
    LR_export_postProcessing = 'doNothing',
    LR_size_doConstrain = false,
  }
  if opts.longEdgePixels then
    s.LR_size_doConstrain = true
    s.LR_size_userWantsConstrain = true
    s.LR_size_units = 'pixels'
    s.LR_size_resizeType = 'longEdge'
    s.LR_size_maxWidth = opts.longEdgePixels
    s.LR_size_maxHeight = opts.longEdgePixels
    s.LR_size_doNotEnlarge = true
  end
  return s
end

function Common.buildCommand(c)
  local q = Common.shellQuote
  local parts = {
    q(c.python), '-m', 'fm', 'merge',
    '--frames-file', q(c.framesFile), '--follow', '--expected', tostring(c.expected),
    '--out', q(c.out),
    '--progress-file', q(c.progress), '--result-file', q(c.result), '--cancel-file', q(c.cancel),
    '--error-file', q(c.error), '--idle-timeout', tostring(c.idleTimeout or 600),
    '--align', c.align or 'auto', '--sharpness', tostring(c.sharpness or 2),
    '--delete-inputs',
  }
  local cmd = 'cd ' .. q(c.enginePath) .. ' && ' .. table.concat(parts, ' ')
  if c.log then cmd = cmd .. ' > ' .. q(c.log) .. ' 2>&1' end
  return cmd
end

-- LrTasks.execute may hand back a raw wait status (exit code << 8) on macOS.
function Common.normalizeExit(code)
  if code and code > 255 then return math.floor(code / 256) end
  return code
end

function Common.isAbsolute(path)
  return type(path) == 'string' and path:sub(1, 1) == '/'
end

-- First non-blank line of a text, trimmed; nil when there is none.
function Common.firstLine(text)
  if not text then return nil end
  for line in text:gmatch('[^\r\n]+') do
    local trimmed = line:match('^%s*(.-)%s*$')
    if trimmed ~= '' then return trimmed end
  end
  return nil
end

-- "done|total|stage|message"; total is '?' when unknown
function Common.parseProgress(text)
  if not text then return nil end
  local done, total, stage, message = text:match('^(%d+)|([%d%?]+)|([^|]*)|(.*)$')
  if not done then return nil end
  return { done = tonumber(done), total = tonumber(total), stage = stage, message = message }
end

-- The result file is JSON written by the engine; read the few fields the plugin needs.
function Common.parseResult(text)
  if not text then return nil end
  local result = { warnings = {} }
  result.output = text:match('"output":%s*"([^"]*)"')
  result.frames = tonumber(text:match('"frames":%s*(%d+)'))
  result.seconds = tonumber(text:match('"seconds":%s*([%d%.]+)'))
  local list = text:match('"warnings":%s*%[(.-)%]')
  if list then
    for w in list:gmatch('"([^"]*)"') do result.warnings[#result.warnings + 1] = w end
  end
  return result
end

return Common
