package org.bukkit.scheduler;

import org.bukkit.plugin.Plugin;

/** Compile-only stub. Real interface provided by the server at runtime. */
public interface BukkitScheduler {
    BukkitTask runTaskTimerAsynchronously(Plugin plugin, Runnable task, long delay, long period);
}
