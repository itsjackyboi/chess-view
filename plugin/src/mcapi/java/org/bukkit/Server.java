package org.bukkit;

import org.bukkit.plugin.PluginManager;
import org.bukkit.scheduler.BukkitScheduler;

/** Compile-only stub. Real interface provided by the server at runtime. */
public interface Server {
    PluginManager getPluginManager();
    BukkitScheduler getScheduler();
}
