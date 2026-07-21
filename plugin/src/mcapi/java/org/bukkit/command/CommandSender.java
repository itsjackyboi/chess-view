package org.bukkit.command;

/** Compile-only stub. Real interface provided by the server at runtime. */
public interface CommandSender {
    void sendMessage(String message);
    boolean hasPermission(String name);
    boolean isOp();
}
