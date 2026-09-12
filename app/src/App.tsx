import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { LiveScreen } from '~/screens/LiveScreen';

export default function App() {
  return (
    <SafeAreaProvider>
      {/* Light content throughout: the app is dark-first by design, since the
          primary surface is a camera feed. */}
      <StatusBar style="light" />
      <LiveScreen />
    </SafeAreaProvider>
  );
}
