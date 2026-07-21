package gg.pintland.audio;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.event.ClickEvent;
import net.kyori.adventure.text.event.HoverEvent;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextDecoration;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;

/**
 * On join, sends the player a clickable chat link to their personal audio tab,
 * with their signed token embedded, and seeds their current region so audio
 * starts as soon as they open it.
 */
public final class JoinListener implements Listener {

    private final TokenSigner signer;
    private final String webBaseUrl;
    private final RegionTracker tracker;

    public JoinListener(TokenSigner signer, String webBaseUrl, RegionTracker tracker) {
        this.signer = signer;
        // Trim a trailing slash so we don't produce "//?token=".
        this.webBaseUrl = webBaseUrl.endsWith("/") ? webBaseUrl.substring(0, webBaseUrl.length() - 1) : webBaseUrl;
        this.tracker = tracker;
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent event) {
        Player player = event.getPlayer();
        String token = signer.sign(player.getUniqueId().toString());
        String link = webBaseUrl + "/?token=" + token;

        Component message = Component.text()
                .append(Component.text("♫ Pintland Audio ", NamedTextColor.GOLD, TextDecoration.BOLD))
                .append(Component.text("— ", NamedTextColor.GRAY))
                .append(Component.text("[Click here to open your music]", NamedTextColor.AQUA, TextDecoration.UNDERLINED)
                        .clickEvent(ClickEvent.openUrl(link))
                        .hoverEvent(HoverEvent.showText(Component.text("Opens a browser tab that plays regional music"))))
                .build();

        player.sendMessage(message);

        // Seed current region on the next tick so movement tracking has state.
        tracker.seed(player);
    }
}
