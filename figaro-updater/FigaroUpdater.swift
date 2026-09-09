import Cocoa
import UserNotifications

// Figaro Updater — tiny menu-bar helper.
// Shows a download-arrow icon ONLY when a newer Jan/Figaro release is
// available. Click -> menu -> "Download & Install", or use the notification
// action. Runs ~/figaro-update/figaro-update.py, which installs the new
// release while preserving the Figaro icon/name/signature and all user data.

let logPath = NSString("~/Library/Logs/figaro-updater.log").expandingTildeInPath
let script = NSString("~/figaro-update/figaro-update.py").expandingTildeInPath

func log(_ msg: String) {
    let line = "\(Date()) \(msg)\n"
    if let h = FileHandle(forWritingAtPath: logPath) {
        h.seekToEndOfFile()
        h.write(line.data(using: .utf8)!)
        try? h.close()
    } else {
        try? line.data(using: .utf8)?.write(to: URL(fileURLWithPath: logPath))
    }
}

struct UpdateStatus {
    var installed: String = ""
    var latest: String = ""
    var available: Bool = false
    var error: String = ""
}

func runChecker() -> UpdateStatus {
    var st = UpdateStatus()
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    p.arguments = [script, "check"]
    let out = Pipe()
    p.standardOutput = out
    p.standardError = Pipe()
    do {
        try p.run()
        p.waitUntilExit()
        let data = out.fileHandleForReading.readDataToEndOfFile()
        let text = String(data: data, encoding: .utf8) ?? ""
        let last = text.split(separator: "\n").last.map(String.init) ?? ""
        st.available = last.contains("UPDATE AVAILABLE")
        for kv in last.split(separator: "|") {
            let pair = kv.trimmingCharacters(in: .whitespaces).split(separator: ":", maxSplits: 1)
            guard pair.count == 2 else { continue }
            let k = pair[0].trimmingCharacters(in: .whitespaces)
            let v = pair[1].trimmingCharacters(in: .whitespaces)
            if k == "installed" { st.installed = v }
            if k == "latest" { st.latest = v }
        }
    } catch {
        st.error = "\(error)"
    }
    return st
}

class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    var statusItem: NSStatusItem?
    var statusVisible = false
    var last = UpdateStatus()
    var lastRefresh = Date.distantPast
    var updating = false
    var timer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        requestNotifAuth()
        UNUserNotificationCenter.current().delegate = self
        refreshFromQueue(force: true)
        timer = Timer.scheduledTimer(withTimeInterval: 3600 * 4, repeats: true) { _ in
            self.refreshFromQueue(force: true)
        }
    }

    func requestNotifAuth() {
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
    }

    @discardableResult
    func refresh(force: Bool) -> UpdateStatus {
        if !force && Date().timeIntervalSince(lastRefresh) < 600 { return last }
        let st = runChecker()
        last = st
        lastRefresh = Date()
        return st
    }

    func refreshFromQueue(force: Bool) {
        DispatchQueue.global(qos: .userInitiated).async {
            let st = self.refresh(force: force)
            DispatchQueue.main.async {
                self.apply(st)
            }
        }
    }

    func apply(_ st: UpdateStatus) {
        if st.available && !updating {
            showStatus()
            notify(st)
        } else {
            hideStatus()
        }
    }

    func showStatus() {
        guard !statusVisible else { return }
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let btn = item.button {
            if let img = NSImage(systemSymbolName: "arrow.down.circle.fill",
                                 accessibilityDescription: "Figaro update available") {
                img.isTemplate = true
                btn.image = img
            } else {
                btn.title = "⬇"
            }
        }
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        statusItem = item
        statusVisible = true
    }

    func hideStatus() {
        guard let item = statusItem else { return }
        NSStatusBar.system.removeStatusItem(item)
        statusItem = nil
        statusVisible = false
    }

    func notify(_ st: UpdateStatus) {
        guard updating == false else { return }
        let content = UNMutableNotificationContent()
        content.title = "Figaro update available"
        content.body = "Figaro \(st.latest) is ready to download (you have \(st.installed))."
        content.sound = .default
        let dl = UNNotificationAction(identifier: "download", title: "Download & Install")
        let later = UNNotificationAction(identifier: "later", title: "Later")
        content.categoryIdentifier = "FIGARO_UPDATE"
        let cat = UNNotificationCategory(identifier: "FIGARO_UPDATE",
                                         actions: [dl, later], intentIdentifiers: [],
                                         options: [])
        UNUserNotificationCenter.current().setNotificationCategories([cat])
        let req = UNNotificationRequest(identifier: "figaro-update-\(st.latest)",
                                        content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req)
    }

    func startUpdate() {
        guard !updating else { return }
        updating = true
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [script, "update"]
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        try? p.run()
        log("update started from UI")
        statusItem?.button?.toolTip = "Updating Figaro..."
        // The updater quits and relaunches Figaro itself; we just re-check later.
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 90) {
            DispatchQueue.main.async {
                self.updating = false
                self.refreshFromQueue(force: true)
            }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.actionIdentifier == "download" {
            startUpdate()
        }
        completionHandler()
    }
}

extension AppDelegate: NSMenuDelegate {
    func menuWillOpen(_ menu: NSMenu) {
        let st = refresh(force: false)
        menu.removeAllItems()
        if st.available {
            let title = NSMenuItem(title: "Figaro \(st.latest) available", action: nil, keyEquivalent: "")
            title.isEnabled = false
            menu.addItem(title)
            menu.addItem(.separator())
            let dl = NSMenuItem(title: "Download & Install Figaro \(st.latest)",
                                action: #selector(downloadClicked), keyEquivalent: "d")
            dl.target = self
            menu.addItem(dl)
            let why = NSMenuItem(title: "You have \(st.installed)", action: nil, keyEquivalent: "")
            why.isEnabled = false
            menu.addItem(why)
        } else if updating {
            menu.addItem(NSMenuItem(title: "Updating Figaro…", action: nil, keyEquivalent: ""))
        } else {
            let up = NSMenuItem(title: "Figaro \(st.installed) is up to date", action: nil, keyEquivalent: "")
            up.isEnabled = false
            menu.addItem(up)
            if !st.error.isEmpty {
                let e = NSMenuItem(title: "Check failed: \(st.error)", action: nil, keyEquivalent: "")
                e.isEnabled = false
                menu.addItem(e)
            }
        }
        menu.addItem(.separator())
        let chk = NSMenuItem(title: "Check Now", action: #selector(checkNow), keyEquivalent: "r")
        chk.target = self
        menu.addItem(chk)
        let quit = NSMenuItem(title: "Quit Figaro Updater", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
    }

    @objc func downloadClicked() { startUpdate() }
    @objc func checkNow() { refreshFromQueue(force: true); log("manual check") }
    @objc func quit() { NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()