package org.bukkit.entity;

import java.util.UUID;
import net.kyori.adventure.text.Component;
import org.bukkit.Location;
import org.bukkit.command.CommandSender;

/** Compile-only stub. Real interface provided by the server at runtime. */
public interface Player extends CommandSender {
    UUID getUniqueId();
    String getName();
    Location getLocation();

    /** From Adventure's Audience; Paper implements it. */
    void sendMessage(Component message);
}
