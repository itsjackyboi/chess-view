package org.bukkit.plugin;

import org.bukkit.event.Listener;

/** Compile-only stub. Real interface provided by the server at runtime. */
public interface PluginManager {
    void registerEvents(Listener listener, Plugin plugin);
}
