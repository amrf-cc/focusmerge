return {
  LrSdkVersion = 6.0,
  LrSdkMinimumVersion = 6.0,
  LrToolkitIdentifier = 'com.riveraferran.focusmerge',
  LrPluginName = 'Focus Merge',
  LrPluginInfoProvider = 'PluginInfoProvider.lua',
  LrLibraryMenuItems = {
    { title = 'Focus Merge Selected Photos', file = 'FocusMerge.lua', enabledWhen = 'photosSelected' },
    { title = 'Focus Merge Settings...', file = 'Settings.lua' },
  },
  LrExportMenuItems = {
    { title = 'Focus Merge Selected Photos', file = 'FocusMerge.lua', enabledWhen = 'photosSelected' },
    { title = 'Focus Merge Settings...', file = 'Settings.lua' },
  },
  VERSION = { major = 1, minor = 2, revision = 0, build = 1 },
}
