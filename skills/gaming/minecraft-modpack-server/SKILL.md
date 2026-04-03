---
name: minecraft-modpack-server
description: Set up a modded Minecraft server from a CurseForge/Modrinth server pack zip. Covers NeoForge/Forge install, Java version, JVM tuning, firewall, LAN config, backups, and launch scripts.
tags: [minecraft, gaming, server, neoforge, forge, modpack]
---

# Minecraft Modpack Server Setup

## When to use
- User wants to set up a modded Minecraft server from a server pack zip
- User needs help with NeoForge/Forge server configuration
- User asks about Minecraft server performance tuning or backups

## Gather User Preferences First
Before starting setup, ask the user for:
- **Server name / MOTD** — what should it say in the server list?
- **Seed** — specific seed or random?
- **Difficulty** — peaceful / easy / normal / hard?
- **Gamemode** — survival / creative / adventure?
- **Online mode** — true (Mojang auth, legit accounts) or false (LAN/cracked friendly)?
- **Player count** — how many players expected? (affects RAM & view distance tuning)
- **RAM allocation** — or let agent decide based on mod count & available RAM?
- **View distance / simulation distance** — or let agent pick based on player count & hardware?
- **PvP** — on or off?
- **Whitelist** — open server or whitelist only?
- **Backups** — want automated backups? How often?

Use sensible defaults if the user doesn't care, but always ask before generating the config.

## Steps

### 1. Download & Inspect the Pack
```bash
mkdir -p ~/minecraft-server
cd ~/minecraft-server
wget -O serverpack.zip "<URL>"
unzip -o serverpack.zip -d server
ls server/
```
Look for: `startserver.sh`, installer jar (neoforge/forge), `user_jvm_args.txt`, `mods/` folder.
Check the script to determine: mod loader type, version, and required Java version.

### 2. Install Java
- Minecraft 1.21+ → Java 21: `sudo apt install openjdk-21-jre-headless`
- Minecraft 1.18-1.20 → Java 17: `sudo apt install openjdk-17-jre-headless`
- Minecraft 1.16 and below → Java 8: `sudo apt install openjdk-8-jre-headless`
- Verify: `java -version`

### 3. Install the Mod Loader
Most server packs include an install script. Use the INSTALL_ONLY env var to install without launching:
```bash
cd ~/minecraft-server/server
ATM10_INSTALL_ONLY=true bash startserver.sh
# Or for generic Forge packs:
# java -jar forge-*-installer.jar --installServer
```
This downloads libraries, patches the server jar, etc.

### 4. Accept EULA
```bash
echo "eula=true" > ~/minecraft-server/server/eula.txt
```

### 5. Configure server.properties
Key settings for modded/LAN:
```properties
motd=\u00a7b\u00a7lServer Name \u00a7r\u00a78| \u00a7aModpack Name
server-port=25565
online-mode=true          # false for LAN without Mojang auth
enforce-secure-profile=true  # match online-mode
difficulty=hard            # most modpacks balance around hard
allow-flight=true          # REQUIRED for modded (flying mounts/items)
spawn-protection=0         # let everyone build at spawn
max-tick-time=180000       # modded needs longer tick timeout
enable-command-block=true
```

Performance settings (scale to hardware):
```properties
# 2 players, beefy machine:
view-distance=16
simulation-distance=10

# 4-6 players, moderate machine:
view-distance=10
simulation-distance=6

# 8+ players or weaker hardware:
view-distance=8
simulation-distance=4
```

### 6. Tune JVM Args (user_jvm_args.txt)
Scale RAM to player count and mod count. Rule of thumb for modded:
- 100-200 mods: 6-12GB
- 200-350+ mods: 12-24GB
- Leave at least 8GB free for the OS/other tasks

```
-Xms12G
-Xmx24G
-XX:+UseG1GC
-XX:+ParallelRefProcEnabled
-XX:MaxGCPauseMillis=200
-XX:+UnlockExperimentalVMOptions
-XX:+DisableExplicitGC
-XX:+AlwaysPreTouch
-XX:G1NewSizePercent=30
-XX:G1MaxNewSizePercent=40
-XX:G1HeapRegionSize=8M
-XX:G1ReservePercent=20
-XX:G1HeapWastePercent=5
-XX:G1MixedGCCountTarget=4
-XX:InitiatingHeapOccupancyPercent=15
-XX:G1MixedGCLiveThresholdPercent=90
-XX:G1RSetUpdatingPauseTimePercent=5
-XX:SurvivorRatio=32
-XX:+PerfDisableSharedMem
-XX:MaxTenuringThreshold=1
```

### 7. Open Firewall
```bash
sudo ufw allow 25565/tcp comment "Minecraft Server"
```
Check with: `sudo ufw status | grep 25565`

### 8. Create Launch Script
```bash
cat > ~/start-minecraft.sh << 'EOF'
#!/bin/bash
cd ~/minecraft-server/server
java @user_jvm_args.txt @libraries/net/neoforged/neoforge/<VERSION>/unix_args.txt nogui
EOF
chmod +x ~/start-minecraft.sh
```
Note: For Forge (not NeoForge), the args file path differs. Check `startserver.sh` for the exact path.

### 9. Set Up Automated Backups
Create backup script:
```bash
cat > ~/minecraft-server/backup.sh << 'SCRIPT'
#!/bin/bash
SERVER_DIR="$HOME/minecraft-server/server"
BACKUP_DIR="$HOME/minecraft-server/backups"
WORLD_DIR="$SERVER_DIR/world"
MAX_BACKUPS=24
mkdir -p "$BACKUP_DIR"
[ ! -d "$WORLD_DIR" ] && echo "[BACKUP] No world folder" && exit 0
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
BACKUP_FILE="$BACKUP_DIR/world_${TIMESTAMP}.tar.gz"
echo "[BACKUP] Starting at $(date)"
tar -czf "$BACKUP_FILE" -C "$SERVER_DIR" world
SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[BACKUP] Saved: $BACKUP_FILE ($SIZE)"
BACKUP_COUNT=$(ls -1t "$BACKUP_DIR"/world_*.tar.gz 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
    REMOVE=$((BACKUP_COUNT - MAX_BACKUPS))
    ls -1t "$BACKUP_DIR"/world_*.tar.gz | tail -n "$REMOVE" | xargs rm -f
    echo "[BACKUP] Pruned $REMOVE old backup(s)"
fi
echo "[BACKUP] Done at $(date)"
SCRIPT
chmod +x ~/minecraft-server/backup.sh
```

Add hourly cron:
```bash
(crontab -l 2>/dev/null | grep -v "minecraft/backup.sh"; echo "0 * * * * $HOME/minecraft-server/backup.sh >> $HOME/minecraft-server/backups/backup.log 2>&1") | crontab -
```

## Pitfalls

### Known Failure Modes and Error Patterns

**1. Server crashes on startup with `java.lang.OutOfMemoryError: Metaspace` or `Java heap space`**
- **Cause**: Insufficient RAM allocation in `user_jvm_args.txt` for the mod count
- **Fix**: Increase `-Xmx` — for 200+ mods use at least `-Xmx12G`. Also check no other processes are consuming RAM: `free -h`
- **Workaround**: If hardware is limited, reduce mod count or use Aikar's flags for better GC pressure management

**2. `java.net.BindException: Address already in use` on port 25565**
- **Cause**: Another server instance is still running, or another service occupies the port
- **Fix**: `sudo lsof -i :25565` to find the process, then `kill <PID>`. Change port if needed: `server-port=25566` in server.properties

**3. Players get "Outdated server" or "Outdated client" on connect**
- **Cause**: Client modpack version doesn't match the server pack version
- **Fix**: Both client and server must use the EXACT same modpack version number. Check with `grep -r "version=" mods/` or ask user to confirm their client version

**4. `net.neoforged.fml.loading.moddiscovery.InvalidModFileException` or similar mod loading errors**
- **Cause**: Corrupted download during `startserver.sh` install, incompatible mod, or wrong mod loader version
- **Fix**: Delete the `mods/` and `libraries/` directories, re-run the installer with `ATM10_INSTALL_ONLY=true bash startserver.sh`. Check `logs/latest.log` for the specific mod causing the conflict

**5. Server watchdog kills the process: "A single server tick took X.XX seconds"**
- **Cause**: Heavy mod interaction, worldgen lag spike, or insufficient `max-tick-time`
- **Fix**: Set `max-tick-time=300000` (5 min) or `-1` (disable watchdog entirely). If recurring, check for known problem mods in the pack's issue tracker. Pre-generate world chunks with a chunk pregenerator mod to avoid worldgen stalls.

**6. `eula.txt` not found or EULA not accepted error**
- **Cause**: EULA file missing or `eula=false` (default from download)
- **Fix**: Always run `echo "eula=true" > server/eula.txt` before first launch, even if the pack claims to auto-accept

**7. JVM crashes with SIGSEGV or "A fatal error has been detected"**
- **Cause**: Incompatible JDK version, or JVM flags not supported on this architecture
- **Fix**: Match Java version to Minecraft version exactly (1.21+→Java 21, 1.18-1.20→Java 17, ≤1.16→Java 8). Remove aggressive G1GC flags and start with `-Xmx8G -XX:+UseG1GC` as a baseline.

### General Pitfalls
- ALWAYS set `allow-flight=true` for modded — mods with jetpacks/flight will kick players otherwise
- `max-tick-time=180000` or higher — modded servers often have long ticks during worldgen
- First startup is SLOW (several minutes for big packs) — don't panic
- "Can't keep up!" warnings on first launch are normal, settles after initial chunk gen
- If online-mode=false, set enforce-secure-profile=false too or clients get rejected
- The pack's startserver.sh often has an auto-restart loop — make a clean launch script without it
- Delete the world/ folder to regenerate with a new seed
- Some packs have env vars to control behavior (e.g., ATM10 uses ATM10_JAVA, ATM10_RESTART, ATM10_INSTALL_ONLY)

## Verification
- `pgrep -fa neoforge` or `pgrep -fa minecraft` to check if running
- Check logs: `tail -f ~/minecraft-server/server/logs/latest.log`
- Look for "Done (Xs)!" in the log = server is ready
- Test connection: player adds server IP in Multiplayer
