import type { TextStyle } from 'react-native';

/**
 * Design tokens.
 *
 * Dark-first and committed to it: the primary surface is a live camera feed, so a
 * light theme would mean painting bright panels over whatever the camera sees. The
 * overlay is built to sit on top of video -- translucent, high-contrast, and never
 * covering the middle of the frame where the board is.
 */

export const color = {
  /** Behind the camera, and for non-camera screens. */
  background: '#0B0E13',
  surface: 'rgba(17, 21, 28, 0.92)',
  /** Panels that sit directly on the camera feed. */
  overlay: 'rgba(11, 14, 19, 0.78)',
  overlayBorder: 'rgba(255, 255, 255, 0.10)',

  text: '#F2F4F8',
  textMuted: '#9BA4B4',
  textFaint: '#5C6675',

  /** The evaluation bar: white's share and black's share. */
  evalWhite: '#EEF1F6',
  evalBlack: '#1A1F27',

  accent: '#5B9DFF',
  good: '#4ADE80',
  warn: '#FBBF24',
  bad: '#F87171',

  /** Applied to analysis that is no longer known to be current. */
  staleOverlay: 'rgba(11, 14, 19, 0.55)',
} as const;

export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radius = {
  sm: 6,
  md: 10,
  lg: 16,
  pill: 999,
} as const;

// Typed as TextStyle rather than `as const`: a const assertion makes fontVariant a
// readonly tuple, which React Native's mutable FontVariant[] will not accept.
export const font = {
  /** Evaluations and move lists: tabular figures stop numbers jittering as they
   *  update several times a second. */
  mono: { fontFamily: 'Menlo', fontVariant: ['tabular-nums'] },
  display: { fontSize: 34, fontWeight: '700', letterSpacing: -0.5 },
  title: { fontSize: 20, fontWeight: '600' },
  body: { fontSize: 15, fontWeight: '400' },
  label: { fontSize: 12, fontWeight: '600', letterSpacing: 0.8 },
  caption: { fontSize: 13, fontWeight: '400' },
} satisfies Record<string, TextStyle>;

/** How long the evaluation bar takes to travel to a new value.
 *  Fast enough to feel responsive, slow enough that the bar reads as moving
 *  rather than teleporting on every depth update. */
export const EVAL_BAR_DURATION_MS = 320;
