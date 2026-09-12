/**
 * Runtime configuration.
 *
 * The service URL is an environment variable rather than a constant so the same
 * build can point at a local server during development and the deployed one
 * otherwise. There is no local-machine dependency at runtime: the default is the
 * deployed service.
 */

import { Platform } from 'react-native';

const FALLBACK_URL = 'wss://api.chessview.app/v1/session';

export const SERVICE_URL =
  process.env.EXPO_PUBLIC_CHESSVIEW_URL?.trim() || FALLBACK_URL;

export const APP_VERSION = '0.1.0';

export const PLATFORM: 'ios' | 'android' = Platform.OS === 'ios' ? 'ios' : 'android';
