package gg.pintland.audio;

import java.lang.reflect.Method;
import java.util.function.Predicate;
import java.util.logging.Level;
import java.util.logging.Logger;
import org.bukkit.Location;

/**
 * Talks to WorldGuard entirely by reflection, so the plugin needs no WorldGuard
 * (or WorldEdit) classes at compile time and does not shade them — WorldGuard is
 * provided by the server at runtime, exactly as intended.
 *
 * <p>Mirrors the direct API call chain:
 * <pre>
 *   WorldGuard.getInstance().getPlatform().getRegionContainer().createQuery()
 *             .getApplicableRegions(BukkitAdapter.adapt(location))
 * </pre>
 * Handles are resolved lazily on first use (so WorldGuard is fully loaded) and
 * cached. If WorldGuard is missing or its API is incompatible, region resolution
 * degrades to {@code "default"} (silence) and logs once instead of throwing.
 */
final class WorldGuardBridge {

    private final Logger logger;

    private volatile boolean initialised;
    private boolean available;
    private boolean warned;

    private Method mGetInstance;      // static WorldGuard.getInstance()
    private Method mGetPlatform;      // WorldGuard#getPlatform()
    private Method mGetRegionContainer; // WorldGuardPlatform#getRegionContainer()
    private Method mCreateQuery;      // RegionContainer#createQuery()
    private Method mAdapt;            // static BukkitAdapter.adapt(org.bukkit.Location)
    private Method mGetApplicableRegions; // RegionQuery#getApplicableRegions(weLocation)
    private Method mGetId;            // ProtectedRegion#getId()
    private Method mGetPriority;      // ProtectedRegion#getPriority()

    WorldGuardBridge(Logger logger) {
        this.logger = logger;
    }

    private synchronized void init() {
        if (initialised) return;
        initialised = true;
        try {
            Class<?> worldGuard = Class.forName("com.sk89q.worldguard.WorldGuard");
            mGetInstance = worldGuard.getMethod("getInstance");
            Object instance = mGetInstance.invoke(null);

            mGetPlatform = instance.getClass().getMethod("getPlatform");
            Object platform = mGetPlatform.invoke(instance);

            mGetRegionContainer = platform.getClass().getMethod("getRegionContainer");
            Object container = mGetRegionContainer.invoke(platform);

            mCreateQuery = container.getClass().getMethod("createQuery");
            Object query = mCreateQuery.invoke(container);

            Class<?> bukkitAdapter = Class.forName("com.sk89q.worldedit.bukkit.BukkitAdapter");
            mAdapt = bukkitAdapter.getMethod("adapt", Location.class);

            Class<?> weLocation = Class.forName("com.sk89q.worldedit.util.Location");
            mGetApplicableRegions = query.getClass().getMethod("getApplicableRegions", weLocation);

            available = true;
        } catch (Throwable t) {
            available = false;
            warnOnce("WorldGuard API not available — regions will resolve to default (silence).", t);
        }
    }

    /**
     * Resolve the id of the highest-priority applicable region for which
     * {@code hasMapping} is true, or {@code "default"} if none.
     */
    String resolveRegion(Location location, Predicate<String> hasMapping) {
        if (!initialised) init();
        if (!available) return "default";
        try {
            Object instance = mGetInstance.invoke(null);
            Object platform = mGetPlatform.invoke(instance);
            Object container = mGetRegionContainer.invoke(platform);
            Object query = mCreateQuery.invoke(container);
            Object weLocation = mAdapt.invoke(null, location);
            Object applicable = mGetApplicableRegions.invoke(query, weLocation);

            String best = null;
            int bestPriority = Integer.MIN_VALUE;
            for (Object region : (Iterable<?>) applicable) {
                if (mGetId == null) {
                    mGetId = region.getClass().getMethod("getId");
                    mGetPriority = region.getClass().getMethod("getPriority");
                }
                String id = (String) mGetId.invoke(region);
                int priority = (Integer) mGetPriority.invoke(region);
                if (hasMapping.test(id) && priority > bestPriority) {
                    bestPriority = priority;
                    best = id;
                }
            }
            return best == null ? "default" : best;
        } catch (Throwable t) {
            warnOnce("WorldGuard region query failed — resolving to default.", t);
            return "default";
        }
    }

    private void warnOnce(String message, Throwable t) {
        if (warned) return;
        warned = true;
        logger.log(Level.WARNING, "[PintlandAudio] " + message + " (" + t + ")");
    }
}
