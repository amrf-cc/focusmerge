local LrView = import 'LrView'
local LrBinding = import 'LrBinding'
local LrDialogs = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrPrefs = import 'LrPrefs'
local Common = require 'Common'

LrFunctionContext.callWithContext('FocusMergeSettings', function(context)
  local prefs = LrPrefs.prefsForPlugin()
  local f = LrView.osFactory()
  local props = LrBinding.makePropertyTable(context)
  for key, value in pairs(Common.withDefaults(prefs)) do props[key] = value end

  local function row(label, control)
    return f:row { f:static_text { title = label, width = 150, alignment = 'right' }, control }
  end

  local contents = f:column {
    bind_to_object = props,
    spacing = f:control_spacing(),
    row('Output size', f:popup_menu { value = LrView.bind('sizePercent'), items = {
      { title = 'Full size (100%)', value = 100 },
      { title = '75%', value = 75 },
      { title = '50% (fast draft)', value = 50 } } }),
    row('Colour space', f:popup_menu { value = LrView.bind('colorSpace'), items = {
      { title = 'ProPhoto RGB', value = 'ProPhotoRGB' },
      { title = 'Adobe RGB', value = 'AdobeRGB' },
      { title = 'sRGB', value = 'sRGB' } } }),
    row('Sharpness bias', f:popup_menu { value = LrView.bind('sharpness'), items = {
      { title = 'Smooth (1) - very noisy frames', value = 1 },
      { title = 'Balanced (2) - default', value = 2 },
      { title = 'Crisp (3)', value = 3 } } }),
    row('Alignment', f:popup_menu { value = LrView.bind('align'), items = {
      { title = 'Automatic', value = 'auto' },
      { title = 'None (tripod / already aligned)', value = 'none' } } }),
    row('Work folder', f:edit_field { value = LrView.bind('workFolder'), width_in_chars = 40 }),
    f:row { f:spacer { width = 150 }, f:static_text {
      title = 'Empty = system temp. Point at a fast external drive if the main disk is nearly full.',
      font = '<system/small>' } },
    row('Output folder', f:edit_field { value = LrView.bind('outputFolder'), width_in_chars = 40 }),
    f:row { f:spacer { width = 150 }, f:static_text {
      title = 'Empty = next to the first selected photo.', font = '<system/small>' } },
    row('Python (advanced)', f:edit_field { value = LrView.bind('pythonPath'), width_in_chars = 40 }),
  }

  local result = LrDialogs.presentModalDialog { title = 'Focus Merge Settings', contents = contents }
  if result == 'ok' then
    for key in pairs(Common.PREF_DEFAULTS) do prefs[key] = props[key] end
  end
end)
