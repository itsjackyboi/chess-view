import { CameraView, useCameraPermissions } from "expo-camera";
import * as ImageManipulator from "expo-image-manipulator";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Dimensions,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

// ─── CONFIG ──────────────────────────────────────────────────────────────────
// Run `ipconfig` on your PC, use your IPv4 address (e.g. 192.168.1.42)
// Make sure your phone and PC are on the same WiFi network
const SERVER_URL = "http://10.0.2.2:5050";
// ─────────────────────────────────────────────────────────────────────────────

const PIECE_TO_FEN: Record<string, string> = {
  white_king:   "K", white_queen:  "Q", white_rook:   "R",
  white_bishop: "B", white_knight: "N", white_pawn:   "P",
  black_king:   "k", black_queen:  "q", black_rook:   "r",
  black_bishop: "b", black_knight: "n", black_pawn:   "p",
};

type Detection = {
  class: string;
  x: number;
  y: number;
  width: number;
  height: number;
  confidence: number;
};

function detectionsToFen(detections: Detection[]): string {
  const board: string[][] = Array.from({ length: 8 }, () => Array(8).fill(""));

  for (const d of detections) {
    const pieceChar = PIECE_TO_FEN[d.class];
    if (!pieceChar) continue;
    const col = Math.min(7, Math.floor(d.x * 8));
    const row = Math.min(7, Math.floor(d.y * 8));
    board[row][col] = pieceChar;
  }

  return board
    .map((rank) => {
      let str = "";
      let empty = 0;
      for (const cell of rank) {
        if (cell === "") {
          empty++;
        } else {
          if (empty > 0) { str += empty; empty = 0; }
          str += cell;
        }
      }
      if (empty > 0) str += empty;
      return str;
    })
    .join("/");
}

export default function ChessScannerScreen() {
  const [permission, requestPermission] = useCameraPermissions();
  const [scanning, setScanning]         = useState(false);
  const [fen, setFen]                   = useState<string | null>(null);
  const [error, setError]               = useState<string | null>(null);
  const [detections, setDetections]     = useState<Detection[]>([]);
  const [ping, setPing]                 = useState<"unknown" | "ok" | "fail">("unknown");
  const cameraRef                       = useRef<CameraView>(null);

  async function checkServer() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const res = await fetch(`${SERVER_URL}/health`, { signal: controller.signal });
    clearTimeout(timer);
    const json = await res.json();
    setPing(json.status === "ok" ? "ok" : "fail");
  } catch {
    setPing("fail");
  }
}

  async function captureAndScan() {
  if (!cameraRef.current || scanning) return;
  setScanning(true);
  setFen(null);
  setError(null);
  setDetections([]);

  try {
    const photo = await cameraRef.current.takePictureAsync({ quality: 0.85 });
    if (!photo) throw new Error("Camera returned no photo.");

    const resized = await ImageManipulator.manipulateAsync(
      photo.uri,
      [{ resize: { width: 1280 } }],
      { compress: 0.85, format: ImageManipulator.SaveFormat.JPEG, base64: true }
    );

    if (!resized.base64) throw new Error("Failed to encode image.");

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(`${SERVER_URL}/detect`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ image: resized.base64 }),
      signal:  controller.signal,
    });
    clearTimeout(timer);

    if (!response.ok) {
      const msg = await response.text();
      throw new Error(`Server error ${response.status}: ${msg}`);
    }

    const result = await response.json();
    setDetections(result.predictions ?? []);
    setFen(detectionsToFen(result.predictions ?? []));
  } catch (err: any) {
    setError(err.message ?? "Unknown error");
  } finally {
    setScanning(false);
  }
}

  if (!permission) return <View style={styles.container} />;

  if (!permission.granted) {
    return (
      <View style={styles.container}>
        <Text style={styles.permText}>Camera access is needed to scan the board.</Text>
        <Pressable style={styles.btn} onPress={requestPermission}>
          <Text style={styles.btnText}>Grant Permission</Text>
        </Pressable>
      </View>
    );
  }

  const { width: SW } = Dimensions.get("window");
  const overlaySize   = SW - 32;

  return (
    <View style={styles.container}>

      {/* Server ping indicator */}
      <Pressable onPress={checkServer} style={styles.pingRow}>
        <View style={[
          styles.pingDot,
          ping === "ok"   && { backgroundColor: "#00e676" },
          ping === "fail" && { backgroundColor: "#ff5252" },
        ]} />
        <Text style={styles.pingText}>
          {ping === "unknown" ? "Tap to ping server" : ping === "ok" ? "Server connected" : "Server unreachable"}
        </Text>
      </Pressable>

      {/* Camera */}
      <View style={[styles.cameraWrap, { width: overlaySize, height: overlaySize }]}>
        <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" />

        {/* Corner brackets */}
        {(["tl", "tr", "bl", "br"] as const).map((pos) => (
          <View key={pos} style={[styles.corner, styles[pos]]} />
        ))}

        {/* Detection dots */}
        {detections.map((d, i) => (
          <View
            key={i}
            style={{
              position:        "absolute",
              left:            d.x * overlaySize - 8,
              top:             d.y * overlaySize - 8,
              width:           16,
              height:          16,
              borderRadius:    8,
              backgroundColor: d.class.startsWith("white")
                ? "rgba(255,255,255,0.85)"
                : "rgba(0,0,0,0.85)",
              borderWidth:  1.5,
              borderColor:  d.class.startsWith("white") ? "#000" : "#fff",
            }}
          />
        ))}
      </View>

      {/* Scan button */}
      <Pressable
        style={[styles.btn, scanning && styles.btnDisabled]}
        onPress={captureAndScan}
        disabled={scanning}
      >
        {scanning
          ? <ActivityIndicator color="#0d0d0d" />
          : <Text style={styles.btnText}>📷  Scan Board</Text>
        }
      </Pressable>

      {/* FEN output */}
      {fen && (
        <View style={styles.fenBox}>
          <Text style={styles.fenLabel}>{detections.length} pieces detected</Text>
          <Text style={styles.fenText} selectable>{fen}</Text>
        </View>
      )}

      {error && (
        <View style={[styles.fenBox, styles.errorBox]}>
          <Text style={styles.fenLabel}>Error</Text>
          <Text style={[styles.fenText, { color: "#ff6b6b" }]}>{error}</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex:            1,
    backgroundColor: "#0d0d0d",
    alignItems:      "center",
    justifyContent:  "center",
    gap:             16,
    paddingHorizontal: 16,
  },
  cameraWrap: {
    borderRadius:    12,
    overflow:        "hidden",
    position:        "relative",
    backgroundColor: "#1a1a1a",
  },
  corner: {
    position:    "absolute",
    width:       28,
    height:      28,
    borderColor: "#00e5ff",
    borderWidth: 3,
  },
  tl: { top: 8,    left:  8, borderBottomWidth: 0, borderRightWidth: 0, borderTopLeftRadius:     4 },
  tr: { top: 8,    right: 8, borderBottomWidth: 0, borderLeftWidth:  0, borderTopRightRadius:    4 },
  bl: { bottom: 8, left:  8, borderTopWidth:    0, borderRightWidth: 0, borderBottomLeftRadius:  4 },
  br: { bottom: 8, right: 8, borderTopWidth:    0, borderLeftWidth:  0, borderBottomRightRadius: 4 },

  btn: {
    backgroundColor:  "#00e5ff",
    paddingHorizontal: 32,
    paddingVertical:   14,
    borderRadius:      10,
    minWidth:          180,
    alignItems:        "center",
  },
  btnDisabled: { opacity: 0.5 },
  btnText: {
    color:         "#0d0d0d",
    fontWeight:    "700",
    fontSize:      16,
    letterSpacing: 0.5,
  },

  fenBox: {
    backgroundColor: "#1a1a1a",
    borderRadius:    10,
    padding:         14,
    width:           "100%",
    borderWidth:     1,
    borderColor:     "#2a2a2a",
  },
  errorBox: { borderColor: "#ff525233" },
  fenLabel: {
    color:         "#888",
    fontSize:      11,
    marginBottom:  6,
    fontWeight:    "600",
    letterSpacing: 0.8,
    textTransform: "uppercase",
  },
  fenText: {
    color:      "#e0e0e0",
    fontFamily: "monospace",
    fontSize:   13,
    lineHeight: 20,
  },

  pingRow: {
    flexDirection: "row",
    alignItems:    "center",
    gap:           8,
  },
  pingDot: {
    width:        10,
    height:       10,
    borderRadius: 5,
    backgroundColor: "#444",
  },
  pingText: {
    color:    "#888",
    fontSize: 13,
  },
} as any);