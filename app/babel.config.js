module.exports = function (api) {
  api.cache(true);
  return {
    presets: ['babel-preset-expo'],
    plugins: [
      // Frame processors run on a separate worklet runtime; this plugin is what
      // compiles the worklet functions. Reanimated's plugin must stay last.
      'react-native-worklets-core/plugin',
      'react-native-reanimated/plugin',
    ],
  };
};
