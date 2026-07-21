package gg.pintland.audio;

import java.net.URI;
import java.util.Map;
import java.util.logging.Level;
import java.util.logging.Logger;
import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;

/**
 * WebSocket client to the relay's /plugin endpoint. Sends region_change and
 * player_quit messages as JSON. Reconnection is driven externally by
 * {@link PintlandAudioPlugin} on a repeating scheduler task so it stays on the
 * plugin's control and survives relay restarts.
 */
public final class RelayClient extends WebSocketClient {

    private final Logger logger;

    public RelayClient(URI serverUri, String sharedSecret, Logger logger) {
        super(serverUri, Map.of("x-plugin-secret", sharedSecret));
        this.logger = logger;
        // Detect half-open connections so the scheduler can reconnect.
        setConnectionLostTimeout(30);
    }

    @Override
    public void onOpen(ServerHandshake handshake) {
        logger.info("[PintlandAudio] Connected to relay");
    }

    @Override
    public void onMessage(String message) {
        // Relay -> plugin messages (e.g. client_status) are informational only.
        logger.fine("[PintlandAudio] relay: " + message);
    }

    @Override
    public void onClose(int code, String reason, boolean remote) {
        logger.warning("[PintlandAudio] Relay connection closed (" + code + "): " + reason);
    }

    @Override
    public void onError(Exception ex) {
        logger.log(Level.WARNING, "[PintlandAudio] Relay connection error: " + ex.getMessage());
    }

    /** Send a region_change for a player. Volume is 0-100. */
    public void sendRegionChange(String uuid, String region, String url, int volume) {
        emit(Json.object(
                "type", "region_change",
                "player", uuid,
                "region", region,
                "url", url,
                "volume", volume));
    }

    /** Send a player_quit so the relay can drop that player's state. */
    public void sendPlayerQuit(String uuid) {
        emit(Json.object("type", "player_quit", "player", uuid));
    }

    private void emit(String json) {
        try {
            if (isOpen()) {
                send(json);
            }
        } catch (Exception e) {
            logger.log(Level.FINE, "[PintlandAudio] Failed to send to relay", e);
        }
    }
}
