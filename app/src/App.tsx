import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { LiveScreen } from '~/screens/LiveScreen';

export default function App() {
  return (
    // Required at the root for the calibration overlay's draggable corner handles;
    // gestures below an unwrapped tree silently do nothing.
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        {/* Light content throughout: the app is dark-first by design, since the
            primary surface is a camera feed. */}
        <StatusBar style="light" />
        <LiveScreen />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
