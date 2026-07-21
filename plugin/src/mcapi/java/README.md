# Compile-only Bukkit API stubs

These are **not** the real Bukkit/Paper API. They are minimal stubs of exactly
the API surface `PintlandAudio` uses, so the plugin can compile in an environment
that cannot reach the PaperMC Maven repository.

They live in a separate `mcapi` source set wired as `compileOnly`, so they are on
the compile classpath but are **excluded from the shaded jar**. At runtime the real
Paper server provides these classes (identical fully-qualified names), which is the
standard "provided dependency" model — the stubs are only a compile-time shadow.

If you build in an environment with PaperMC access, you can delete this source set
and switch `build.gradle` back to `compileOnly 'io.papermc.paper:paper-api:...'`.

The API used here (JavaPlugin, PlayerMoveEvent, configuration, scheduler) has been
stable across Bukkit/Spigot/Paper for many years, so these signatures match 1.21.x.
