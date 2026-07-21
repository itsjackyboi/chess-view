package gg.pintland.audio;

import java.io.File;
import java.net.URI;
import java.util.HashMap;
import java.util.Map;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.configuration.ConfigurationSection;
import org.bukkit.configuration.file.YamlConfiguration;
import org.bukkit.entity.Player;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.scheduler.BukkitTask;

/**
 * Entry point. Loads config + regions, opens (and keeps open) the relay
 * WebSocket connection, and registers the region/join listeners.
 */
public final class PintlandAudioPlugin extends JavaPlugin {

    private RelayClient relay;
    private BukkitTask reconnectTask;

    private String relayUrl;
    private String sharedSecret;
    private int reconnectSeconds;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        saveResource("regions.yml", /* replace */ false);

        loadRelaySettings();

        TokenSigner signer = new TokenSigner(getConfig().getString("token-secret", ""));
        Map<String, RegionAudio> regions = loadRegions();
        RegionAudio defaultAudio = loadDefaultAudio();

        connectRelay();

        RegionTracker tracker = new RegionTracker(
                relay,
                regions,
                defaultAudio,
                getConfig().getInt("hysteresis.consecutive-crossings", 2),
                getConfig().getInt("hysteresis.blocks-past-boundary", 2));

        String webBaseUrl = getConfig().getString("web-base-url", "https://audio.example.com");
        getServer().getPluginManager().registerEvents(tracker, this);
        getServer().getPluginManager().registerEvents(new JoinListener(signer, webBaseUrl, tracker), this);

        // Keep the relay connection alive: reconnect if it isn't open.
        reconnectTask = getServer().getScheduler().runTaskTimerAsynchronously(this, () -> {
            if (relay == null || relay.isClosed()) {
                connectRelay();
            }
        }, reconnectSeconds * 20L, reconnectSeconds * 20L);

        getLogger().info("PintlandAudio enabled (" + regions.size() + " mapped regions)");
    }

    @Override
    public void onDisable() {
        if (reconnectTask != null) reconnectTask.cancel();
        if (relay != null) {
            try { relay.closeBlocking(); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        }
    }

    private void loadRelaySettings() {
        relayUrl = getConfig().getString("relay.url", "ws://localhost:8080/plugin");
        sharedSecret = getConfig().getString("relay.shared-secret", "");
        reconnectSeconds = Math.max(1, getConfig().getInt("relay.reconnect-seconds", 5));
    }

    private synchronized void connectRelay() {
        try {
            // A closed WebSocketClient cannot be reused; make a fresh one.
            relay = new RelayClient(new URI(relayUrl), sharedSecret, getLogger());
            relay.connect();
        } catch (Exception e) {
            getLogger().warning("Failed to connect to relay: " + e.getMessage());
        }
    }

    private Map<String, RegionAudio> loadRegions() {
        Map<String, RegionAudio> map = new HashMap<>();
        YamlConfiguration yaml = YamlConfiguration.loadConfiguration(new File(getDataFolder(), "regions.yml"));
        ConfigurationSection section = yaml.getConfigurationSection("regions");
        if (section != null) {
            for (String key : section.getKeys(false)) {
                ConfigurationSection r = section.getConfigurationSection(key);
                if (r == null) continue;
                map.put(key, new RegionAudio(r.getString("url", ""), r.getInt("volume", 100)));
            }
        }
        return map;
    }

    private RegionAudio loadDefaultAudio() {
        YamlConfiguration yaml = YamlConfiguration.loadConfiguration(new File(getDataFolder(), "regions.yml"));
        ConfigurationSection d = yaml.getConfigurationSection("default");
        if (d == null) return new RegionAudio("", 0);
        return new RegionAudio(d.getString("url", ""), d.getInt("volume", 0));
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length == 0) {
            sender.sendMessage("/pintlandaudio <reload|link>");
            return true;
        }
        switch (args[0].toLowerCase()) {
            case "reload":
                if (!sender.hasPermission("pintlandaudio.admin") && !sender.isOp()) {
                    sender.sendMessage("No permission.");
                    return true;
                }
                reloadConfig();
                sender.sendMessage("PintlandAudio config reloaded. Re-enable the plugin to fully apply region/relay changes.");
                return true;
            case "link":
                if (!(sender instanceof Player)) {
                    sender.sendMessage("Only players have a link.");
                    return true;
                }
                Player p = (Player) sender;
                TokenSigner signer = new TokenSigner(getConfig().getString("token-secret", ""));
                String base = getConfig().getString("web-base-url", "https://audio.example.com");
                if (base.endsWith("/")) base = base.substring(0, base.length() - 1);
                sender.sendMessage(base + "/?token=" + signer.sign(p.getUniqueId().toString()));
                return true;
            default:
                sender.sendMessage("/pintlandaudio <reload|link>");
                return true;
        }
    }
}
