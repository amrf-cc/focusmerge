local LrApplication = import 'LrApplication'
local LrTasks = import 'LrTasks'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'
local LrDialogs = import 'LrDialogs'
local LrExportSession = import 'LrExportSession'
local LrProgressScope = import 'LrProgressScope'
local LrFunctionContext = import 'LrFunctionContext'
local LrPrefs = import 'LrPrefs'
local LrDate = import 'LrDate'

local Common = require 'Common'
local MergeFlow = require 'MergeFlow'

LrFunctionContext.postAsyncTaskWithContext('FocusMerge', function(context)
  -- LrTasks.pcall (not pcall): the flow yields while exporting and waiting for the engine.
  local ok, err = LrTasks.pcall(MergeFlow.run, {
    Common = Common,
    LrApplication = LrApplication, LrTasks = LrTasks, LrFileUtils = LrFileUtils,
    LrPathUtils = LrPathUtils, LrDialogs = LrDialogs, LrExportSession = LrExportSession,
    LrProgressScope = LrProgressScope, LrDate = LrDate,
    prefs = LrPrefs.prefsForPlugin(), pluginPath = _PLUGIN.path, context = context,
  })
  if not ok then
    LrDialogs.message('Focus Merge hit an unexpected error', tostring(err), 'critical')
  end
end)
