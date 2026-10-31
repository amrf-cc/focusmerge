--[[ Runs MergeFlow.run against the mock SDK. Expects globals MOCK (config) and PLUGIN_DIR. ]]
package.path = PLUGIN_DIR .. '/?.lua;' .. package.path
local mock = dofile(MOCK.mockPath)
local Common = require 'Common'
local MergeFlow = require 'MergeFlow'

local env = mock.env
MergeFlow.busy = MOCK.preBusy
env.Common = Common
env.prefs = MOCK.prefs or {}
env.pluginPath = PLUGIN_DIR
env.context = {}

mock.spawn(function()
  local ok, err = env.LrTasks.pcall(MergeFlow.run, env)
  if not ok then mock.log.error = tostring(err) end
end)
mock.runAll()
RESULT = mock.log
