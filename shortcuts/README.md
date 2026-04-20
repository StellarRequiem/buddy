# Buddy iOS Shortcuts Pack

Four shortcuts to install on iPhone/iPad. All call buddy over Tailscale.

## Prerequisites
1. Tailscale installed on both Mac Mini and iPhone/iPad, signed into same account
2. Note your Mac's Tailscale IP from the Tailscale app (looks like `100.x.x.x`)
3. Buddy running on the Mac (`launchd` starts it automatically on login)

## Your Tailscale URL
Replace `TAILSCALE_IP` in every shortcut with your actual IP.
Format: `http://100.x.x.x:7437`

---

## Shortcut 1 — "Ask Buddy"
**Trigger:** "Hey Siri, ask buddy" or tap

**Steps to build in Shortcuts app:**
1. New Shortcut → name it "Ask Buddy"
2. Add action: **Ask for Input** → Prompt: "What do you want to ask?" → Input Type: Text
   - Store result in variable: `Question`
3. Add action: **Get Contents of URL**
   - URL: `http://TAILSCALE_IP:7437/siri/ask`
   - Method: POST
   - Request Body: JSON
   - Add field: `message` = Variable `Question`
   - Add field: `session_id` = `siri-ios`
4. Add action: **Speak Text**
   - Text: Contents of URL (from step 3)
5. Add action: **Show Notification** (optional)
   - Title: "Buddy"
   - Body: Contents of URL

**Add to Siri:** Settings → Siri → My Shortcuts → Ask Buddy → Record phrase "ask buddy"

---

## Shortcut 2 — "Buddy Task"
**Trigger:** "Hey Siri, buddy task" or tap

**Steps:**
1. New Shortcut → name it "Buddy Task"
2. Add action: **Ask for Input** → Prompt: "What's the task?" → Input Type: Text
   - Store result: `TaskTitle`
3. Add action: **Get Contents of URL**
   - URL: `http://TAILSCALE_IP:7437/siri/task`
   - Method: POST
   - Request Body: JSON
   - Add field: `title` = Variable `TaskTitle`
4. Add action: **Speak Text** → Contents of URL

---

## Shortcut 3 — "Buddy Status"
**Trigger:** "Hey Siri, buddy status"

**Steps:**
1. New Shortcut → name it "Buddy Status"
2. Add action: **Get Contents of URL**
   - URL: `http://TAILSCALE_IP:7437/siri/status`
   - Method: GET
3. Add action: **Speak Text** → Contents of URL

---

## Shortcut 4 — "Buddy Ping"  
**Trigger:** Quick connectivity check

**Steps:**
1. New Shortcut → name it "Buddy Ping"
2. Add action: **Get Contents of URL**
   - URL: `http://TAILSCALE_IP:7437/siri/ping`
   - Method: GET
3. Add action: **If** → Contents of URL → Contains → "online"
   - True: **Show Notification** → "Buddy is reachable ✅"
   - False: **Show Notification** → "Buddy offline — is the Mac awake? ❌"

---

## Troubleshooting
- **"Connection refused"** → Mac asleep or buddy not running. Enable "Prevent computer from sleeping" in Energy Saver.
- **"No route to host"** → Tailscale not connected on one device. Check both devices in Tailscale app.
- **Slow first response** → Qwen2.5:14b cold start takes ~5s. Normal.
- **Test from Safari first** → Open `http://TAILSCALE_IP:7437/siri/ping` in Safari on iPhone before building shortcuts.

## Wake-on-demand (optional)
Add to your Mac's Energy Saver settings:
- Prevent automatic sleeping: ON
- Wake for network access: ON
This keeps buddy reachable even when the display is off.
