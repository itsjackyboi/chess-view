package org.bukkit.plugin.java;

import java.io.File;
import java.util.logging.Logger;
import org.bukkit.Server;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.configuration.file.FileConfiguration;
import org.bukkit.plugin.Plugin;

/**
 * Compile-only stub of the subset of JavaPlugin the plugin uses. At runtime the
 * real Paper JavaPlugin is the superclass; these bodies never execute.
 */
public abstract class JavaPlugin implements Plugin {
    public Server getServer() { return null; }
    public Logger getLogger() { return null; }
    public File getDataFolder() { return null; }
    public FileConfiguration getConfig() { return null; }
    public void reloadConfig() { }
    public void saveDefaultConfig() { }
    public void saveResource(String resourcePath, boolean replace) { }

    public void onEnable() { }
    public void onDisable() { }
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) { return false; }
}
