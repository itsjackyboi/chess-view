package gg.pintland.audio;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import org.bukkit.Location;
import org.bukkit.block.Block;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerMoveEvent;
import org.bukkit.event.player.PlayerQuitEvent;

/**
 * Watches players crossing block boundaries, resolves their current WorldGuard
 * region to an audio mapping, and fires region_change to the relay — but only
 * once a candidate region has stabilised (hysteresis), so standing in a doorway
 * doesn't rapidly flip music back and forth.
 */
public final class RegionTracker implements Listener {

    /** Per-player region tracking state. */
    private static final class State {
        String currentRegion;        // audio region currently playing (null until first fire)
        String candidateRegion;      // a different region we're considering switching to
        int candidateCrossings;      // consecutive block-crossings the candidate has held
        Block candidateEntryBlock;   // where the candidate region first appeared (approx boundary)
    }

    private final RelayClient relay;
    private final WorldGuardBridge worldGuard;
    private final Map<String, RegionAudio> regions;
    private final RegionAudio defaultAudio;
    private final int consecutiveCrossings;
    private final int blocksPastBoundary;

    private final Map<UUID, State> states = new HashMap<>();

    public RegionTracker(RelayClient relay, WorldGuardBridge worldGuard, Map<String, RegionAudio> regions,
                         RegionAudio defaultAudio, int consecutiveCrossings, int blocksPastBoundary) {
        this.relay = relay;
        this.worldGuard = worldGuard;
        this.regions = regions;
        this.defaultAudio = defaultAudio;
        this.consecutiveCrossings = Math.max(1, consecutiveCrossings);
        this.blocksPastBoundary = Math.max(1, blocksPastBoundary);
    }

    @EventHandler(ignoreCancelled = true)
    public void onMove(PlayerMoveEvent event) {
        // Performance-critical: only proceed when the player crossed a block
        // boundary. Same-block movement (looking around, sub-block steps) exits early.
        if (event.getFrom().getBlock().equals(event.getTo().getBlock())) {
            return;
        }
        handleCrossing(event.getPlayer(), event.getTo());
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        states.remove(event.getPlayer().getUniqueId());
    }

    /** Public so a fresh join can be seeded to the player's starting region. */
    public void seed(Player player) {
        handleCrossing(player, player.getLocation());
    }

    private void handleCrossing(Player player, Location to) {
        UUID id = player.getUniqueId();
        State st = states.computeIfAbsent(id, k -> new State());
        String resolved = resolveRegion(player, to);

        // First observation: fire immediately so the player gets audio on join.
        if (st.currentRegion == null) {
            fire(player, resolved);
            st.currentRegion = resolved;
            resetCandidate(st);
            return;
        }

        // Still in the same audio region — nothing to consider.
        if (resolved.equals(st.currentRegion)) {
            resetCandidate(st);
            return;
        }

        // A different region than what's playing. Track it as a candidate.
        if (!resolved.equals(st.candidateRegion)) {
            st.candidateRegion = resolved;
            st.candidateCrossings = 1;
            st.candidateEntryBlock = to.getBlock();
        } else {
            st.candidateCrossings++;
        }

        boolean heldEnough = st.candidateCrossings >= consecutiveCrossings;
        boolean pastBoundary = st.candidateEntryBlock != null
                && horizontalBlocks(st.candidateEntryBlock, to.getBlock()) >= blocksPastBoundary;

        if (heldEnough || pastBoundary) {
            fire(player, resolved);
            st.currentRegion = resolved;
            resetCandidate(st);
        }
    }

    private void resetCandidate(State st) {
        st.candidateRegion = null;
        st.candidateCrossings = 0;
        st.candidateEntryBlock = null;
    }

    private static double horizontalBlocks(Block a, Block b) {
        double dx = a.getX() - b.getX();
        double dz = a.getZ() - b.getZ();
        return Math.hypot(dx, dz);
    }

    /** Resolve the highest-priority applicable region that has an audio mapping. */
    private String resolveRegion(Player player, Location loc) {
        return worldGuard.resolveRegion(loc, regions::containsKey);
    }

    private void fire(Player player, String regionKey) {
        RegionAudio audio = "default".equals(regionKey) ? defaultAudio : regions.get(regionKey);
        if (audio == null) audio = defaultAudio;
        relay.sendRegionChange(player.getUniqueId().toString(), regionKey, audio.url, audio.volume);
    }
}
