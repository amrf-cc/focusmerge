local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrPrefs = import 'LrPrefs'
local Common = require 'Common'

local provider = {}

function provider.sectionsForTopOfDialog(f, _propertyTable)
  local prefs = Common.withDefaults(LrPrefs.prefsForPlugin())
  local python = prefs.pythonPath
  if python == '' then
    local home = LrPathUtils.getStandardFilePath('home')
    python = home
    for _, part in ipairs({ 'Library', 'Application Support', 'FocusMerge', 'venv', 'bin', 'python' }) do
      python = LrPathUtils.child(python, part)
    end
  end
  local status
  if LrFileUtils.exists(python) then
    status = 'Engine found: ' .. python
  else
    status = 'Engine NOT found. Run install.sh, or set the Python path in Focus Merge Settings.'
  end
  return {
    { title = 'Focus Merge engine', f:row { f:static_text { title = status, width_in_chars = 70 } } },
  }
end

return provider
