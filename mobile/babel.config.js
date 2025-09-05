module.exports = function (api) {
  api.cache(true)
  return {
    presets: ['babel-preset-expo'],
    plugins: [
      [
        'module-resolver',
        {
          root: ['.'],
          alias: {
            '@': './src'
          }
        }
      ],
      // Reanimated plugin disabled temporarily to avoid native crash in Expo Go
    ]
  }
}
