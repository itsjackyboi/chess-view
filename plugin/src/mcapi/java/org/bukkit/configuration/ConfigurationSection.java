package org.bukkit.configuration;

import java.util.Set;

/** Compile-only stub. Real interface provided by the server at runtime. */
public interface ConfigurationSection {
    Set<String> getKeys(boolean deep);
    String getString(String path);
    String getString(String path, String def);
    int getInt(String path);
    int getInt(String path, int def);
    ConfigurationSection getConfigurationSection(String path);
}
