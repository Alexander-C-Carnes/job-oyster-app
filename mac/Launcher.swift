// Job Oyster.app: starts the bundled web app (Python, in Contents/Resources) and opens it in the browser.
//
// The app's files are read-only inside the bundle; everything personal is in the profile folder
// (~/JobOyster, or $JOBPIPE_HOME), which the server makes on first start. Quitting the app stops the server,
// and with it any runs going (a search, Make résumé); it asks first when there are some.
// Dock icon plus a menu-bar item. The item lists runs going, recently finished, résumés ready to send, and
// roles at the Profile › Menu Bar Settings fit floor (7 by default) since yesterday; choosing one opens it in the browser.
// On start it checks GitHub for a newer release, and Check for Updates… does so any time. A signed release
// is downloaded and can replace this copy of the app. The profile folder is never part of that copy.
import AppKit
import Foundation

/// Where a menu-bar row opens: a role, Logs, or just the app.
final class MenuOpen: NSObject {
    let job: String
    let tab: String
    init(job: String = "", tab: String = "") { self.job = job; self.tab = tab }
}

struct MenuSnap {
    struct RunItem {
        var id: String          // pipeline run number, or "score:<job id>"
        var label: String
        var status: String
        var jobId: String
    }
    struct Role {
        var id: String
        var title: String
        var company: String
        var fit: Double?
    }
    var runs: [RunItem] = []
    var ready: [Role] = []
    var newRoles: [Role] = []
    var fitFloor = 7

    static func parse(_ obj: [String: Any]) -> MenuSnap {
        var snap = MenuSnap()
        if let n = obj["fit_floor"] as? Int, n > 0 { snap.fitFloor = n }
        snap.runs = (obj["runs"] as? [[String: Any]] ?? []).compactMap { row in
            guard let status = row["status"] as? String else { return nil }
            let id: String
            if let n = row["id"] as? Int { id = String(n) }
            else if let s = row["id"] as? String { id = s }
            else { return nil }
            return RunItem(id: id, label: row["label"] as? String ?? "", status: status, jobId: row["job_id"] as? String ?? "")
        }
        snap.ready = roles(obj["ready"])
        snap.newRoles = roles(obj["new_roles"], withFit: true)
        return snap
    }

    static func roles(_ raw: Any?, withFit: Bool = false) -> [Role] {
        (raw as? [[String: Any]] ?? []).compactMap { row in
            guard let id = row["id"] as? String else { return nil }
            var fit: Double?
            if withFit {
                if let n = row["fit"] as? Double { fit = n }
                else if let n = row["fit"] as? Int { fit = Double(n) }
            }
            return Role(id: id, title: row["title"] as? String ?? "", company: row["company"] as? String ?? "", fit: fit)
        }
    }
}

struct CheckedApp {
    var app: URL?
    var problem: String?   // why it can't replace this copy; nil when `app` is a signed build of the right version
}

let defaultPort = 8765

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, URLSessionDownloadDelegate {
    var server: Process?
    var url: URL?
    var logURL: URL!
    var quitting = false
    var replacing = false          // an update is swapping this copy: don't ask about runs a second time
    let info = Bundle.main.infoDictionary ?? [:]
    var updateItems: [NSMenuItem] = []
    var statusItem: NSStatusItem?
    var statusMenu: NSMenu?
    var menuTimer: Timer?
    var snap = MenuSnap()
    var menuLoaded = false
    var menuError: String?          // last /api/menu failure; nil when the snap is good
    var knownRuns: [MenuSnap.RunItem] = []   // last good runs list, for "recently finished"
    var recentRuns: [(item: MenuSnap.RunItem, ended: Date)] = []
    let recentFor: TimeInterval = 45
    var checkingUpdate = false
    var downloading = false
    var downloadTitle = ""
    var downloads: URLSession?
    var downloadVersion = ""
    var downloadPage: URL?
    var downloadDir: URL?
    var downloadedDMG: URL?
    var downloadError: String?

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        installStatusItem()
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Job Oyster")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        logURL = logs.appendingPathComponent("server.log")
        start()
        offerToTrashOldApp()
        checkForUpdate()
    }

    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows: Bool) -> Bool {
        openInBrowser(nil)
        return false
    }

    // Quitting stops the runs going and drops the waiting ones (the server does, on SIGINT), so say so first.
    // Only for the server this app started: quitting leaves one started some other way (`oyster run`) alone.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if replacing { return .terminateNow }
        guard let s = server, s.isRunning, let going = runsGoing() else { return .terminateNow }
        let a = NSAlert()
        a.alertStyle = .warning
        a.messageText = going.prefix(1).uppercased() + going.dropFirst()
        a.informativeText = "Quitting Job Oyster stops them: résumés being made are cut short, and the ones waiting "
            + "their turn won't start. To let them finish, keep Job Oyster open."
        a.addButton(withTitle: "Quit and Stop Runs")
        a.addButton(withTitle: "Keep Running")
        return a.runModal() == .alertFirstButtonReturn ? .terminateNow : .terminateCancel
    }

    // A call to this app's own server. The token is the one in the page URL.
    func apiRequest(_ path: String, timeout: TimeInterval = 5) -> URLRequest? {
        guard let u = url, let comps = URLComponents(url: u, resolvingAgainstBaseURL: false),
              let token = comps.queryItems?.first(where: { $0.name == "token" })?.value,
              let api = URL(string: "http://127.0.0.1:\(comps.port ?? defaultPort)\(path)") else { return nil }
        var req = URLRequest(url: api)
        req.timeoutInterval = timeout
        req.setValue(token, forHTTPHeaderField: "X-Jobpipe-Token")
        return req
    }

    // "2 runs are going and 1 is waiting their turn", or nil when none are (or the server doesn't answer).
    func runsGoing() -> String? {
        guard let req = apiRequest("/api/runs/going", timeout: 3) else { return nil }
        let done = DispatchSemaphore(value: 0)
        var text: String?
        URLSession.shared.dataTask(with: req) { data, resp, _ in
            if (resp as? HTTPURLResponse)?.statusCode == 200, let data = data,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               ((obj["running"] as? Int) ?? 0) + ((obj["waiting"] as? Int) ?? 0) > 0 {
                text = obj["text"] as? String
            }
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 4)
        return text
    }

    func applicationWillTerminate(_ note: Notification) {
        quitting = true
        if let s = server, s.isRunning { s.interrupt(); s.waitUntilExit() }
    }

    // MARK: server

    func start() {
        let res = Bundle.main.resourceURL!
        var python = res.appendingPathComponent("python/bin/python3")
        var appDir = res.appendingPathComponent("app")
        let port = Int(ProcessInfo.processInfo.environment["OYSTER_PORT"] ?? ProcessInfo.processInfo.environment["JOBSEARCH_PORT"] ?? "") ?? defaultPort

        if answers(port: port) {                     // already running (another copy of the app, or `oyster run`)
            if !menuRouteExists(port: port) {
                fail("Port \(port) has a Job Oyster that's too old for the menu bar. Stop it and run "
                     + "`oyster` from this checkout (or open a build that includes the menu), then try again.")
                return
            }
            waitForURL(fromLog: nil, port: port)
            return
        }
        // Full DMG builds ship python + app under Resources. The thin `build/Job Oyster Menu.app`
        // used while developing has neither: use the checkout's .venv beside build/.
        if !FileManager.default.isExecutableFile(atPath: python.path) {
            guard let repo = checkoutRoot(),
                  FileManager.default.isExecutableFile(atPath: repo.appendingPathComponent(".venv/bin/python3").path) else {
                fail("Job Oyster couldn't start its server: this copy has no bundled Python. "
                     + "Run `oyster` from the checkout first, or open a full build (mac/build.sh).")
                return
            }
            python = repo.appendingPathComponent(".venv/bin/python3")
            appDir = repo
        }
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let log = try! FileHandle(forWritingTo: logURL)

        let p = Process()
        p.executableURL = python
        p.arguments = ["-m", "jobpipe", "serve", "--port", String(port), "--no-open"]
        p.currentDirectoryURL = appDir
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        // Apps start with a bare PATH; add where Claude Code and Homebrew live.
        env["PATH"] = ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "\(home)/.npm-global/bin",
                       env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"].joined(separator: ":")
        env["PYTHONDONTWRITEBYTECODE"] = "1"       // the bundle is read-only (and signed)
        env["PYTHONUNBUFFERED"] = "1"
        let browsers = res.appendingPathComponent("ms-playwright")
        if FileManager.default.fileExists(atPath: browsers.path) {
            env["PLAYWRIGHT_BROWSERS_PATH"] = browsers.path
        }
        env["OYSTER_APP_VERSION"] = info["CFBundleShortVersionString"] as? String ?? ""
        p.environment = env
        p.standardOutput = log
        p.standardError = log
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.serverStopped(proc.terminationStatus) }
        }
        do { try p.run() } catch {
            fail("Job Oyster couldn't start its server: \(error.localizedDescription)")
            return
        }
        server = p
        waitForURL(fromLog: logURL, port: port)
    }

    // build/Job Oyster Menu.app → the repo that contains jobpipe/ and .venv/
    func checkoutRoot() -> URL? {
        let repo = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
        guard FileManager.default.fileExists(atPath: repo.appendingPathComponent("jobpipe").path) else { return nil }
        return repo
    }

    // The server prints "running at http://127.0.0.1:PORT/?token=…" once it's up; open that.
    func waitForURL(fromLog log: URL?, port: Int) {
        let deadline = Date().addingTimeInterval(120)
        DispatchQueue.global().async { [weak self] in
            guard let self = self else { return }
            while Date() < deadline {
                if let found = self.urlFromLog(log, port: port) {
                    DispatchQueue.main.async { self.url = found; self.openInBrowser(nil); self.fetchMenu() }
                    return
                }
                if let s = self.server, !s.isRunning { return }
                Thread.sleep(forTimeInterval: 0.4)
            }
            DispatchQueue.main.async { self.fail("Job Oyster didn't start within two minutes.") }
        }
    }

    func urlFromLog(_ log: URL?, port: Int) -> URL? {
        if let log = log {
            guard let text = try? String(contentsOf: log, encoding: .utf8),
                  let r = text.range(of: #"http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_\-]+"#, options: .regularExpression)
            else { return nil }
            return URL(string: String(text[r]))
        }
        // Someone else's server: use a token that server accepts (not only $JOBPIPE_HOME, which may be stale).
        let token = menuToken(port: port)
        guard !token.isEmpty else { return nil }
        return URL(string: "http://127.0.0.1:\(port)/?token=\(token)")
    }

    func answers(port: Int) -> Bool {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!)
        req.timeoutInterval = 2
        let done = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { data, resp, _ in
            ok = (resp as? HTTPURLResponse)?.statusCode == 200
                && String(data: data ?? Data(), encoding: .utf8)?.contains("app.js") == true
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 3)
        return ok
    }

    // /api/menu returns 401 without a token when the route exists; 404 means an older server.
    func menuRouteExists(port: Int) -> Bool {
        guard let api = URL(string: "http://127.0.0.1:\(port)/api/menu") else { return false }
        var req = URLRequest(url: api)
        req.timeoutInterval = 2
        let done = DispatchSemaphore(value: 0)
        var code = -1
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            code = (resp as? HTTPURLResponse)?.statusCode ?? -1
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 3)
        return code == 401 || code == 200
    }

    func serverStopped(_ status: Int32) {
        if quitting { return }
        fail("Job Oyster stopped unexpectedly (exit \(status)).")
    }

    func fail(_ message: String) {
        let tail = (try? String(contentsOf: logURL, encoding: .utf8))
            .map { $0.split(separator: "\n").suffix(12).joined(separator: "\n") } ?? ""
        let a = NSAlert()
        a.messageText = message
        a.informativeText = tail.isEmpty ? "See \(logURL.path)." : "From \(logURL.lastPathComponent):\n\n\(tail)"
        a.addButton(withTitle: "Quit")
        a.addButton(withTitle: "Show Log")
        if a.runModal() == .alertSecondButtonReturn { NSWorkspace.shared.activateFileViewerSelecting([logURL]) }
        NSApp.terminate(nil)
    }

    // $JOBPIPE_HOME, else ~/JobOyster, or ~/JobSearch (its name before the rename) until the server moves it.
    func profileFolder() -> URL {
        if let h = ProcessInfo.processInfo.environment["JOBPIPE_HOME"], !h.isEmpty {
            return URL(fileURLWithPath: (h as NSString).expandingTildeInPath)
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        let now = home.appendingPathComponent("JobOyster")
        return FileManager.default.fileExists(atPath: now.path) ? now : home.appendingPathComponent("JobSearch")
    }

    // The app was called Job Search up to v0.1.9, so dragging this one into Applications leaves the old one there.
    // Offer once to move it to the Trash (from where it can be put back).
    func offerToTrashOldApp() {
        let old = URL(fileURLWithPath: "/Applications/Job Search.app")
        let key = "askedToTrashJobSearchApp"
        guard Bundle.main.bundleURL.standardizedFileURL != old.standardizedFileURL,
              FileManager.default.fileExists(atPath: old.path),
              !UserDefaults.standard.bool(forKey: key) else { return }
        UserDefaults.standard.set(true, forKey: key)
        let a = NSAlert()
        a.messageText = "Job Search is now Job Oyster"
        a.informativeText = "The old Job Search app is still in Applications. Move it to the Trash? "
            + "Your profile folder and everything in it stay as they are."
        a.addButton(withTitle: "Move to Trash")
        a.addButton(withTitle: "Keep It")
        guard a.runModal() == .alertFirstButtonReturn else { return }
        NSWorkspace.shared.recycle([old]) { _, error in
            if let error = error {
                DispatchQueue.main.async { self.tell("Couldn't move Job Search to the Trash", error.localizedDescription) }
            }
        }
    }

    // MARK: menu bar

    func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.autosaveName = "JobOysterMenu"   // keeps Command-drag position across launches
        let menu = NSMenu()
        menu.autoenablesItems = false
        menu.delegate = self
        item.menu = menu
        if let button = item.button {
            button.image = menuBarIcon()
            button.image?.isTemplate = true
            button.imagePosition = .imageLeft
            button.imageScaling = .scaleNone
        }
        statusItem = item
        statusMenu = menu
        applyStatusButton()
        scheduleMenuPoll()
    }

    // Bundled MenuBarIcon.png (mac/make_menubar_icon.swift); drawn fallback matches the app icon mark.
    func menuBarIcon() -> NSImage {
        let s = NSSize(width: 18, height: 18)
        if let url = Bundle.main.url(forResource: "MenuBarIcon", withExtension: "png"),
           let disc = NSImage(contentsOf: url) {
            let img = NSImage(size: s)
            img.lockFocus()
            disc.draw(in: NSRect(origin: .zero, size: s), from: .zero, operation: .sourceOver, fraction: 1)
            img.unlockFocus()
            img.isTemplate = true
            img.accessibilityDescription = "Job Oyster"
            return img
        }
        let img = NSImage(size: s, flipped: true) { _ in
            let src = CGRect(x: 220, y: 250, width: 600, height: 600)
            let t = NSAffineTransform()
            t.scaleX(by: s.width / src.width, yBy: s.height / src.height)
            t.translateX(by: -src.minX, yBy: -src.minY)
            t.concat()
            func bowl(_ y: CGFloat, bulge: CGFloat) -> NSBezierPath {
                let p = NSBezierPath()
                p.move(to: NSPoint(x: 250, y: y))
                p.curve(to: NSPoint(x: 800, y: y),
                        controlPoint1: NSPoint(x: 250 + 2.0 / 3 * 270, y: y + 2.0 / 3 * bulge),
                        controlPoint2: NSPoint(x: 800 - 2.0 / 3 * 280, y: y + 2.0 / 3 * bulge))
                p.close()
                return p
            }
            NSColor.black.setFill()
            bowl(560, bulge: 270).fill()
            NSGraphicsContext.saveGraphicsState()
            let lid = NSAffineTransform()
            lid.translateX(by: 250, yBy: 548)
            lid.rotate(byDegrees: -24)
            lid.translateX(by: -250, yBy: -548)
            lid.concat()
            bowl(536, bulge: -270).fill()
            NSGraphicsContext.restoreGraphicsState()
            NSBezierPath(ovalIn: NSRect(x: 540, y: 450, width: 120, height: 120)).fill()
            return true
        }
        img.isTemplate = true
        img.accessibilityDescription = "Job Oyster"
        return img
    }

    // Idle: every 15s. Busy (runs going or waiting): every 4s so short HiringCafe checks still show.
    func scheduleMenuPoll() {
        menuTimer?.invalidate()
        let secs: TimeInterval = snap.runs.isEmpty && menuError == nil ? 15 : 4
        menuTimer = Timer.scheduledTimer(withTimeInterval: secs, repeats: false) { [weak self] _ in
            self?.fetchMenu()
            self?.scheduleMenuPoll()
        }
    }

    func fetchMenu(wait: Bool = false) {
        guard let req = apiRequest("/api/menu") else {
            applyMenuFailure("Job Oyster isn’t responding", reschedule: !wait)
            return
        }
        let done = wait ? DispatchSemaphore(value: 0) : nil
        var next: MenuSnap?
        var err: String?
        URLSession.shared.dataTask(with: req) { data, resp, error in
            defer { done?.signal() }
            let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
            if error != nil || code == -1 {
                err = "Job Oyster isn’t responding"
            } else if code == 401 {
                err = "Job Oyster isn’t responding (wrong token)"
            } else if code == 404 {
                err = "This server is too old for the menu"
            } else if code == 200, let data = data,
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                next = MenuSnap.parse(obj)
            } else {
                err = "Job Oyster isn’t responding"
            }
            if wait { return }
            DispatchQueue.main.async {
                if let parsed = next {
                    self.acceptMenu(parsed)
                } else {
                    self.applyMenuFailure(err ?? "Job Oyster isn’t responding", reschedule: false)
                }
                self.scheduleMenuPoll()
            }
        }.resume()
        if let done = done {
            _ = done.wait(timeout: .now() + 2)
            if let next = next {
                acceptMenu(next)
            } else {
                applyMenuFailure(err ?? "Job Oyster isn’t responding", reschedule: false)
            }
        }
    }

    func acceptMenu(_ parsed: MenuSnap) {
        noteFinished(from: knownRuns, to: parsed.runs)
        knownRuns = parsed.runs
        snap = parsed
        menuLoaded = true
        menuError = nil
        applyStatusButton()
    }

    func applyMenuFailure(_ message: String, reschedule: Bool) {
        let apply = {
            self.menuError = message
            self.menuLoaded = true
            self.applyStatusButton()
            if reschedule { self.scheduleMenuPoll() }
        }
        if Thread.isMainThread { apply() } else { DispatchQueue.main.async(execute: apply) }
    }

    // Token the server on `port` accepts. Tries each candidate profile's web-token against /api/menu
    // so a leftover JOBPIPE_HOME does not blank the menu when ~/JobOyster is the real profile.
    func menuToken(port: Int = defaultPort) -> String {
        for token in tokenCandidates() {
            if menuAccepts(token: token, port: port) { return token }
        }
        return tokenCandidates().first ?? ""
    }

    func tokenCandidates() -> [String] {
        let env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser
        var dirs: [String] = []
        if let d = env["JOBPIPE_DATA_DIR"], !d.isEmpty {
            dirs.append((d as NSString).expandingTildeInPath)
        }
        // Default profiles first: a stale JOBPIPE_HOME is tried after these.
        for name in ["JobOyster", "JobSearch"] {
            dirs.append(home.appendingPathComponent(name).appendingPathComponent("data").path)
        }
        dirs.append(profileFolder().appendingPathComponent("data").path)
        var seen = Set<String>(), out: [String] = []
        for data in dirs where seen.insert(data).inserted {
            let path = (data as NSString).appendingPathComponent("web-token")
            if let raw = try? String(contentsOfFile: path, encoding: .utf8) {
                let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                if !token.isEmpty, !out.contains(token) { out.append(token) }
            }
        }
        return out
    }

    func menuAccepts(token: String, port: Int) -> Bool {
        guard let api = URL(string: "http://127.0.0.1:\(port)/api/menu") else { return false }
        var req = URLRequest(url: api)
        req.timeoutInterval = 2
        req.setValue(token, forHTTPHeaderField: "X-Jobpipe-Token")
        let done = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            ok = (resp as? HTTPURLResponse)?.statusCode == 200
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + 3)
        return ok
    }

    // Open a folder in Finder by bundle, never via NSWorkspace.open(path) — a folder named
    // "Job Oyster" can be matched to this app and reopen the browser.
    func openInFinder(_ folder: URL) {
        DispatchQueue.main.async {
            let finder = URL(fileURLWithPath: "/System/Library/CoreServices/Finder.app")
            NSWorkspace.shared.open([folder], withApplicationAt: finder, configuration: NSWorkspace.OpenConfiguration())
        }
    }

    func applyStatusButton() {
        guard let button = statusItem?.button else { return }
        refreshUpdateItems()
        if downloading {
            button.title = downloadTitle
            button.toolTip = "Downloading update"
            button.setAccessibilityLabel("Job Oyster, downloading update")
            return
        }
        if let err = menuError {
            button.title = ""
            button.toolTip = "Job Oyster: \(err)"
            button.setAccessibilityLabel("Job Oyster, \(err)")
            return
        }
        let going = snap.runs.filter { $0.status == "running" }.count
        let waiting = snap.runs.filter { $0.status == "queued" }.count
        // Run count when something is going/waiting (99+ if somehow more); else a middle dot if ready/new.
        if !snap.runs.isEmpty {
            let n = snap.runs.count
            button.title = n > 99 ? " 99+" : " \(n)"
        } else if !snap.ready.isEmpty || !snap.newRoles.isEmpty {
            button.title = " ·"
        } else {
            button.title = ""
        }
        var bits: [String] = []
        if going > 0 { bits.append("\(going) going") }
        if waiting > 0 { bits.append("\(waiting) in queue") }
        if !snap.ready.isEmpty { bits.append("\(snap.ready.count) ready to send") }
        if !snap.newRoles.isEmpty { bits.append("\(snap.newRoles.count) new at fit score \(snap.fitFloor) or higher") }
        let detail = bits.isEmpty ? "all quiet" : bits.joined(separator: ", ")
        button.toolTip = "Job Oyster: \(detail)"
        button.setAccessibilityLabel("Job Oyster, \(detail)")
    }

    // Runs that left the live list in the last `recentFor` seconds (for a short "Recently finished" section).
    func noteFinished(from previous: [MenuSnap.RunItem], to current: [MenuSnap.RunItem]) {
        let nowIds = Set(current.map(\.id))
        let now = Date()
        for item in previous where !nowIds.contains(item.id) {
            recentRuns.removeAll { $0.item.id == item.id }
            recentRuns.append((item, now))
        }
        recentRuns.removeAll { nowIds.contains($0.item.id) || now.timeIntervalSince($0.ended) >= recentFor }
    }

    func stillRecent() -> [MenuSnap.RunItem] {
        let now = Date()
        recentRuns.removeAll { now.timeIntervalSince($0.ended) >= recentFor }
        return recentRuns.map(\.item)
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        guard menu === statusMenu else { return }
        fetchMenu(wait: true)          // scores and runs as of this open, not the last 15s poll
        menu.removeAllItems()
        if url == nil {
            addNote("Starting Job Oyster…", to: menu)
            menu.addItem(.separator())
            addQuit(to: menu)
            return
        }
        addLink("Open Job Oyster", to: menu)
        menu.addItem(.separator())
        if let err = menuError {
            addNote(err, to: menu)
            let logItem = NSMenuItem(title: "Show Log", action: #selector(showLog(_:)), keyEquivalent: "")
            logItem.target = self
            menu.addItem(logItem)
            menu.addItem(.separator())
            addQuit(to: menu)
            return
        }
        addHeader("Runs", to: menu)
        if !menuLoaded {
            addNote("Updating…", to: menu)
        } else if snap.runs.isEmpty {
            addNote("Nothing running right now", to: menu)
        } else {
            for run in snap.runs.prefix(8) { addRunLink(run, to: menu) }
            if snap.runs.count > 8 { addLink("And \(snap.runs.count - 8) more", tab: "runs", to: menu) }
        }
        let recent = stillRecent()
        if !recent.isEmpty {
            menu.addItem(.separator())
            addHeader("Recently finished", to: menu)
            for run in recent.prefix(8) { addRunLink(run, done: true, to: menu) }
        }
        menu.addItem(.separator())
        addHeader("Résumés ready", to: menu)
        addRoles(snap.ready, tab: "resume", empty: "No résumés waiting to send", to: menu)
        menu.addItem(.separator())
        addHeader("Newest at fit score \(snap.fitFloor)+ since yesterday", to: menu)
        addRoles(snap.newRoles, tab: "posting", empty: "No roles at fit score \(snap.fitFloor)+ since yesterday", showFit: true, to: menu)
        menu.addItem(.separator())
        let check = NSMenuItem(title: downloading ? "Downloading Update…" : "Check for Updates…",
                               action: #selector(checkForUpdates(_:)), keyEquivalent: "")
        check.target = self
        check.isEnabled = !downloading
        menu.addItem(check)
        let profile = NSMenuItem(title: "Show Profile Folder", action: #selector(showProfile(_:)), keyEquivalent: "")
        profile.target = self
        menu.addItem(profile)
        let logItem = NSMenuItem(title: "Show Log", action: #selector(showLog(_:)), keyEquivalent: "")
        logItem.target = self
        menu.addItem(logItem)
        menu.addItem(.separator())
        addQuit(to: menu)
    }

    func addHeader(_ title: String, to menu: NSMenu) {
        if #available(macOS 14.0, *) {
            menu.addItem(.sectionHeader(title: title))
        } else {
            addNote(title, to: menu)
        }
    }

    func addNote(_ title: String, to menu: NSMenu) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        menu.addItem(item)
    }

    func addLink(_ title: String, job: String = "", tab: String = "", to menu: NSMenu) {
        let item = NSMenuItem(title: title, action: #selector(openFromMenu(_:)), keyEquivalent: "")
        item.target = self
        item.representedObject = MenuOpen(job: job, tab: tab)
        menu.addItem(item)
    }

    // Scores open the role's posting; Make résumé opens the résumé; other Logs runs open Logs.
    func addRunLink(_ run: MenuSnap.RunItem, done: Bool = false, to menu: NSMenu) {
        var title = run.label
        if !done, run.status == "queued" { title += " · in queue" }
        title = clip(title)
        if run.id.hasPrefix("score:"), !run.jobId.isEmpty {
            addLink(title, job: run.jobId, tab: "posting", to: menu)
        } else if !run.jobId.isEmpty {
            addLink(title, job: run.jobId, tab: "resume", to: menu)
        } else {
            addLink(title, tab: "runs", to: menu)
        }
    }

    func addQuit(to menu: NSMenu) {
        let item = NSMenuItem(title: "Quit Job Oyster", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        item.target = NSApp
        menu.addItem(item)
    }

    func addRoles(_ roles: [MenuSnap.Role], tab: String, empty: String, showFit: Bool = false,
                   limit: Int = 8, to menu: NSMenu) {
        if !menuLoaded { addNote("Updating…", to: menu); return }
        if roles.isEmpty { addNote(empty, to: menu); return }
        for role in roles.prefix(limit) { addLink(roleLine(role, showFit: showFit), job: role.id, tab: tab, to: menu) }
        if roles.count > limit { addLink("And \(roles.count - limit) more", to: menu) }
    }

    // About 60 characters. Shrink the title first; only then the company. Fit stays whole.
    // With a fit: "8 · Founding Senior Platf… · Broccoli AI".
    func roleLine(_ role: MenuSnap.Role, showFit: Bool) -> String {
        let limit = 60
        let title = role.title.isEmpty ? "Untitled" : role.title
        var prefix = ""
        if showFit, let fit = role.fit { prefix = fitText(fit) + " · " }
        let company = role.company
        var suffix = company.isEmpty ? "" : " · " + company
        var room = max(1, limit - prefix.count - suffix.count)
        var mid = title.count <= room ? title : String(title.prefix(max(1, room - 1))) + "…"
        var line = prefix + mid + suffix
        if line.count > limit, !company.isEmpty {
            let companyRoom = max(1, limit - prefix.count - mid.count - 3)  // " · "
            let co = company.count <= companyRoom ? company : String(company.prefix(max(1, companyRoom - 1))) + "…"
            suffix = " · " + co
            room = max(1, limit - prefix.count - suffix.count)
            mid = title.count <= room ? title : String(title.prefix(max(1, room - 1))) + "…"
            line = prefix + mid + suffix
        }
        return line
    }

    func fitText(_ n: Double) -> String {
        n == n.rounded() ? String(Int(n.rounded())) : String(format: "%.1f", n)
    }

    func clip(_ text: String, _ limit: Int = 90) -> String {
        text.count <= limit ? text : String(text.prefix(limit - 1)) + "…"
    }

    // MARK: menu actions

    @objc func openFromMenu(_ sender: NSMenuItem) {
        let open = sender.representedObject as? MenuOpen
        openApp(job: open?.job ?? "", tab: open?.tab ?? "")
    }

    func openApp(job: String = "", tab: String = "") {
        guard var comps = url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) }) else { return }
        var items = comps.queryItems ?? []
        if !job.isEmpty { items.append(URLQueryItem(name: "job", value: job)) }
        if !tab.isEmpty { items.append(URLQueryItem(name: "tab", value: tab)) }
        comps.queryItems = items
        if let page = comps.url { NSWorkspace.shared.open(page) }
    }

    @objc func openInBrowser(_ sender: Any?) { openApp() }

    // Reveal the profile folder in Finder. Ask Finder by bundle id — NSWorkspace.open on a folder
    // named "Job Oyster" can match this app instead, and activateFileViewerSelecting does little
    // for a directory alone.
    @objc func showProfile(_ sender: Any?) {
        let folder = profileFolder()
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        openInFinder(folder)
    }

    // Open the server log. When this app started the server it writes server.log; an `oyster start`
    // in the background writes app.log instead. After a menu-bar click, yield so the viewer can
    // come forward. Never NSWorkspace.open the parent folder named "Job Oyster" — Launch Services
    // can match that name to this app and reopen it, which opens the browser.
    @objc func showLog(_ sender: Any?) {
        let log = logToShow()
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: Data())
        }
        DispatchQueue.main.async {
            NSWorkspace.shared.activateFileViewerSelecting([log])
            NSWorkspace.shared.open(log)
        }
    }

    func logToShow() -> URL {
        let appLog = logURL.deletingLastPathComponent().appendingPathComponent("app.log")
        if server == nil,
           let n = try? FileManager.default.attributesOfItem(atPath: appLog.path)[.size] as? NSNumber,
           n.intValue > 0 {
            return appLog
        }
        return logURL
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let m = NSMenu()
        m.addItem(menuItem("Open Job Oyster", #selector(openInBrowser(_:)), key: "o"))
        m.addItem(updateMenuItem())
        m.addItem(.separator())
        m.addItem(menuItem("Show Profile Folder", #selector(showProfile(_:))))
        m.addItem(menuItem("Show Log", #selector(showLog(_:))))
        m.addItem(.separator())
        m.addItem(menuItem("Quit Job Oyster", #selector(NSApplication.terminate(_:)), key: "q", target: NSApp))
        appItem.submenu = m
        NSApp.mainMenu = main
        let dock = NSMenu()
        dock.addItem(menuItem("Open Job Oyster", #selector(openInBrowser(_:))))
        dock.addItem(menuItem("Show Profile Folder", #selector(showProfile(_:))))
        dock.addItem(updateMenuItem())
        dockMenu = dock
    }

    func menuItem(_ title: String, _ action: Selector, key: String = "", target: AnyObject? = nil) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = target ?? self
        return item
    }

    func updateMenuItem() -> NSMenuItem {
        let item = menuItem("Check for Updates…", #selector(checkForUpdates(_:)))
        updateItems.append(item)
        return item
    }

    func refreshUpdateItems() {
        let title = downloading ? "Downloading Update…" : "Check for Updates…"
        for item in updateItems {
            item.title = title
            item.isEnabled = !downloading
        }
    }

    var dockMenu: NSMenu?
    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? { dockMenu }

    // MARK: updates

    // Compares this version with the newest GitHub release of JSUpdateRepo ("owner/repo") and offers it.
    // On start it's quiet unless there is one. Check for Updates… (announce) also says when there isn't, when
    // GitHub can't be reached, and that a dev build (0.0.0-dev, from a branch rather than a release tag) isn't
    // updated this way: whoever built one has the source.
    // A signed build for this Mac is downloaded and can replace this copy. Anything else opens the releases
    // page, as before. The profile folder is not part of the app and is never written by an update.
    func checkForUpdate(announce: Bool = false) {
        if downloading {
            if announce {
                tell("A download is already going", "Job Oyster will ask before it replaces this copy. "
                     + "Your profile folder is not changed.")
            }
            return
        }
        if checkingUpdate { return }
        let version = info["CFBundleShortVersionString"] as? String ?? ""
        // Dev builds and copies without JSUpdateRepo keep the menu item (for screenshots) but do nothing.
        guard let repo = info["JSUpdateRepo"] as? String, !repo.isEmpty else { return }
        guard !version.isEmpty, !version.contains("-"),
              let api = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else { return }
        let current = version
        var req = URLRequest(url: api)
        req.timeoutInterval = 15
        req.setValue("JobOyster", forHTTPHeaderField: "User-Agent")
        checkingUpdate = true
        URLSession.shared.dataTask(with: req) { data, _, error in
            let parsed: (obj: [String: Any], tag: String, page: URL)? = {
                guard let data = data,
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let tag = obj["tag_name"] as? String,
                      let page = (obj["html_url"] as? String).flatMap(URL.init(string:)) else { return nil }
                return (obj, tag, page)
            }()
            DispatchQueue.main.async {
                self.checkingUpdate = false
                guard let parsed = parsed else {
                    guard announce else { return }
                    let releases = URL(string: "https://github.com/\(repo)/releases")
                    self.sayOrOpen("Couldn't check for updates", "Job Oyster couldn't reach GitHub"
                                   + (error.map { ": \($0.localizedDescription)" } ?? ".")
                                   + " Check your connection and try again, or look at the releases page yourself.",
                                   page: releases, pageButton: "Releases Page")
                    return
                }
                let latest = parsed.tag.trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
                guard latest.compare(current, options: .numeric) == .orderedDescending else {
                    if announce { self.tell("You're on the latest version", "Job Oyster \(current) is the newest release.") }
                    return
                }
                self.offer(version: latest, page: parsed.page, notes: self.whatsNew(parsed.obj["body"] as? String ?? ""),
                           assets: parsed.obj["assets"] as? [[String: Any]] ?? [], current: current)
            }
        }.resume()
    }

    func offer(version: String, page: URL, notes: String, assets: [[String: Any]], current: String) {
        let a = NSAlert()
        a.messageText = "Job Oyster \(version) is available"
        if canReplaceThisCopy() {
            a.informativeText = notes + "You have \(current). Download it, and Job Oyster will replace this copy and "
                + "open again. Your profile folder stays as it is."
            a.addButton(withTitle: "Download and Replace")
            a.addButton(withTitle: "Later")
            if a.runModal() == .alertFirstButtonReturn { downloadAndReplace(version: version, page: page, assets: assets) }
        } else {
            a.informativeText = notes + "You have \(current). Download the new version, quit this one, and drag the "
                + "new one into Applications. Your profile folder and everything in it stay as they are."
            a.addButton(withTitle: "Download")
            a.addButton(withTitle: "Later")
            if a.runModal() == .alertFirstButtonReturn { NSWorkspace.shared.open(page) }
        }
    }

    // This copy can be swapped for a new one: it isn't a disk image or a translocated download, it isn't inside
    // the profile folder, and both the app and the folder it sits in can be written.
    func canReplaceThisCopy() -> Bool {
        let bundle = Bundle.main.bundleURL.standardizedFileURL
        let path = bundle.path
        if path.contains("AppTranslocation") || path.hasPrefix("/Volumes/") { return false }
        if isInside(path, profileFolder().path) { return false }
        let parent = bundle.deletingLastPathComponent().path
        return FileManager.default.isWritableFile(atPath: parent) && FileManager.default.isWritableFile(atPath: path)
    }

    func isInside(_ path: String, _ folder: String) -> Bool {
        let path = URL(fileURLWithPath: path).standardizedFileURL.path
        let folder = URL(fileURLWithPath: folder).standardizedFileURL.path
        return path == folder || path.hasPrefix(folder + "/")
    }

    func macArch() -> String {
        #if arch(arm64)
        return "arm64"
        #else
        return "x86_64"
        #endif
    }

    func releaseAsset(_ assets: [[String: Any]], version: String) -> URL? {
        let names = ["Job-Oyster-\(version)-\(macArch()).dmg", "Job-Oyster-\(macArch()).dmg"]
        for name in names {
            guard let asset = assets.first(where: { ($0["name"] as? String) == name }),
                  let raw = asset["browser_download_url"] as? String,
                  let url = URL(string: raw), url.scheme == "https",
                  let host = url.host?.lowercased(),
                  host == "github.com" || host.hasSuffix(".githubusercontent.com") else { continue }
            return url
        }
        return nil
    }

    func downloadAndReplace(version: String, page: URL, assets: [[String: Any]]) {
        guard let asset = releaseAsset(assets, version: version) else {
            let which = macArch() == "arm64" ? "Apple silicon" : "Intel"
            sayOrOpen("No download for this Mac", "Job Oyster \(version) doesn't include an \(which) download. "
                      + "The releases page has what there is. Your profile folder is not changed.", page: page)
            return
        }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("JobOysterUpdate-\(UUID().uuidString)")
        do { try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) } catch {
            tell("Couldn't download the update", error.localizedDescription + " Your profile folder is not changed.")
            return
        }
        downloadVersion = version
        downloadPage = page
        downloadDir = dir
        downloadedDMG = nil
        downloadError = nil
        downloading = true
        downloadTitle = " …"
        applyStatusButton()
        var req = URLRequest(url: asset)
        req.timeoutInterval = 120
        req.setValue("JobOyster", forHTTPHeaderField: "User-Agent")
        if downloads == nil { downloads = URLSession(configuration: .default, delegate: self, delegateQueue: nil) }
        downloads?.downloadTask(with: req).resume()
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        let title = totalBytesExpectedToWrite > 0 ? " \(totalBytesWritten * 100 / totalBytesExpectedToWrite)%" : " …"
        DispatchQueue.main.async {
            self.downloadTitle = title
            self.applyStatusButton()
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        if let http = downloadTask.response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            downloadError = "GitHub answered \(http.statusCode)."
            return
        }
        guard let dir = downloadDir else { downloadError = "The download had nowhere to go."; return }
        let dest = dir.appendingPathComponent("Job-Oyster.dmg")
        do {
            if FileManager.default.fileExists(atPath: dest.path) { try FileManager.default.removeItem(at: dest) }
            try FileManager.default.moveItem(at: location, to: dest)
            let size = (try FileManager.default.attributesOfItem(atPath: dest.path)[.size] as? NSNumber)?.int64Value ?? 0
            if size < 1_000_000 { downloadError = "The download was too small to be the app."; return }
            downloadedDMG = dest
        } catch {
            downloadError = error.localizedDescription
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let problem = error?.localizedDescription ?? downloadError
        let dmg = downloadedDMG
        let version = downloadVersion
        let page = downloadPage
        downloadedDMG = nil
        downloadError = nil
        if let problem = problem {
            DispatchQueue.main.async {
                self.downloading = false
                self.applyStatusButton()
                self.cleanupDownload()
                self.sayOrOpen("Couldn't download the update", problem + " Your profile folder is not changed.", page: page)
            }
            return
        }
        guard let dmg = dmg else {
            DispatchQueue.main.async {
                self.downloading = false
                self.applyStatusButton()
                self.cleanupDownload()
                self.sayOrOpen("Couldn't download the update", "The file didn't arrive. Your profile folder is not changed.", page: page)
            }
            return
        }
        DispatchQueue.main.async { self.downloadTitle = " …"; self.applyStatusButton() }
        let checked = installableApp(from: dmg, version: version)
        let going = runsGoing()
        DispatchQueue.main.async {
            if let problem = checked.problem {
                self.downloading = false
                self.applyStatusButton()
                self.cleanupDownload()
                self.sayOrOpen("Couldn't install the update", problem, page: page)
                return
            }
            guard let app = checked.app else {
                self.downloading = false
                self.applyStatusButton()
                self.cleanupDownload()
                return
            }
            self.confirmReplace(app: app, version: version, going: going)
        }
    }

    // Mount the disk image, copy the app out of it, and keep it only when it is this app, this version,
    // built for this Mac, and signed with a Developer ID. An unsigned release is left for the releases page.
    func installableApp(from dmg: URL, version: String) -> CheckedApp {
        guard let dir = downloadDir else { return CheckedApp(problem: "The download had nowhere to go.") }
        let mount = dir.appendingPathComponent("mnt")
        do { try FileManager.default.createDirectory(at: mount, withIntermediateDirectories: true) } catch {
            return CheckedApp(problem: error.localizedDescription)
        }
        _ = runCommand("/usr/bin/xattr", ["-d", "com.apple.quarantine", dmg.path])
        let attached = runCommand("/usr/bin/hdiutil", ["attach", "-nobrowse", "-readonly", "-mountpoint", mount.path, dmg.path])
        defer { _ = runCommand("/usr/bin/hdiutil", ["detach", mount.path, "-quiet"]) }
        guard attached.status == 0 else {
            let why = attached.text.trimmingCharacters(in: .whitespacesAndNewlines)
            return CheckedApp(problem: "The download didn't open as a disk image" + (why.isEmpty ? "." : ": \(why)"))
        }
        let inside = mount.appendingPathComponent("Job Oyster.app")
        guard FileManager.default.fileExists(atPath: inside.path) else {
            return CheckedApp(problem: "The disk image had no Job Oyster app in it.")
        }
        let copy = dir.appendingPathComponent("Job Oyster.app")
        let copied = runCommand("/usr/bin/ditto", [inside.path, copy.path])
        guard copied.status == 0 else { return CheckedApp(problem: "The app couldn't be copied out of the disk image.") }
        return checkDownloadedApp(copy, version: version)
    }

    func checkDownloadedApp(_ app: URL, version: String) -> CheckedApp {
        guard let got = Bundle(url: app), got.bundleIdentifier == Bundle.main.bundleIdentifier else {
            return CheckedApp(problem: "The downloaded app isn't Job Oyster, so it wasn't installed.")
        }
        let ver = got.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        guard ver == version else {
            let said = ver.isEmpty ? "an unknown version" : ver
            return CheckedApp(problem: "The downloaded app says it's \(said), not \(version), so it wasn't installed.")
        }
        let exe = app.appendingPathComponent("Contents/MacOS/JobOyster")
        let info = runCommand("/usr/bin/file", ["-b", exe.path]).text
        let want = macArch() == "arm64" ? "arm64" : "x86_64"
        guard info.contains(want) else {
            return CheckedApp(problem: "The downloaded app isn't built for this Mac, so it wasn't installed.")
        }
        guard runCommand("/usr/bin/codesign", ["--verify", "--deep", "--strict", app.path]).status == 0 else {
            return CheckedApp(problem: "The download's signature didn't check, so it wasn't installed. "
                              + "You can get it from the releases page. Your profile folder is not changed.")
        }
        let signed = runCommand("/usr/bin/codesign", ["-dvvv", app.path]).text
        guard signed.contains("Authority=Developer ID Application:") else {
            return CheckedApp(problem: "Job Oyster \(version) isn't signed, so it can't replace this copy. "
                              + "Download it from the releases page, quit this one, and drag the new one into Applications. "
                              + "Your profile folder is not changed.")
        }
        return CheckedApp(app: app)
    }

    func confirmReplace(app: URL, version: String, going: String?) {
        downloading = false
        applyStatusButton()
        let a = NSAlert()
        a.messageText = "Replace this copy with Job Oyster \(version)?"
        var detail = "Job Oyster will quit and open again. Your profile folder (\(profileDisplay())) is not changed."
        if let going = going {
            a.alertStyle = .warning
            detail += "\n\n\(going.prefix(1).uppercased() + going.dropFirst()). Replacing the app stops them: "
                + "résumés being made are cut short, and the ones waiting their turn won't start."
        }
        a.informativeText = detail
        a.addButton(withTitle: "Replace and Relaunch")
        a.addButton(withTitle: "Not Now")
        guard a.runModal() == .alertFirstButtonReturn else { cleanupDownload(); return }
        swapIn(app)
    }

    // Quit, then swap the .app for the signed copy. The helper is given only the new app, this app, and the
    // download's folder. The profile folder is not one of them.
    func swapIn(_ newApp: URL) {
        let dest = Bundle.main.bundleURL.standardizedFileURL
        let cleanup = newApp.deletingLastPathComponent().standardizedFileURL
        let temp = FileManager.default.temporaryDirectory.standardizedFileURL.path
        guard dest.pathExtension == "app", !isInside(dest.path, profileFolder().path),
              cleanup.path.hasPrefix(temp), cleanup.lastPathComponent.hasPrefix("JobOysterUpdate-") else {
            cleanupDownload()
            tell("Couldn't replace this copy", "This copy isn't one the update can replace. "
                 + "Drag the new one into Applications. Your profile folder is not changed.")
            return
        }
        let scriptDir = FileManager.default.temporaryDirectory.appendingPathComponent("JobOysterUpdate-swap")
        let script = scriptDir.appendingPathComponent("replace.sh")
        let body = """
        #!/bin/bash
        # Swaps the Job Oyster app after it quits. The only paths it receives are this app, the new one, and
        # the download folder. It does not touch the profile folder.
        set -uo pipefail
        pid="$1"; dest="$2"; src="$3"; cleanup="$4"
        while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
        sleep 0.5
        parent=$(dirname "$dest")
        stage="$parent/.JobOyster.update"
        backup="$parent/.JobOyster.previous"
        rm -rf "$stage"
        if ! ditto "$src" "$stage"; then rm -rf "$stage"; open "$dest" || true; exit 1; fi
        if ! mv "$dest" "$backup"; then rm -rf "$stage"; open "$dest" || true; exit 1; fi
        if ! mv "$stage" "$dest"; then mv "$backup" "$dest" || true; open "$dest" || true; exit 1; fi
        rm -rf "$backup"
        xattr -dr com.apple.quarantine "$dest" || true
        open "$dest"
        rm -rf "$cleanup"
        """
        do {
            try FileManager.default.createDirectory(at: scriptDir, withIntermediateDirectories: true)
            try body.write(to: script, atomically: true, encoding: .utf8)
            let logFile = logURL.deletingLastPathComponent().appendingPathComponent("update.log")
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
            let log = try FileHandle(forWritingTo: logFile)
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/nohup")
            p.arguments = ["/bin/bash", script.path, String(ProcessInfo.processInfo.processIdentifier),
                           dest.path, newApp.standardizedFileURL.path, cleanup.path]
            p.standardOutput = log
            p.standardError = log
            p.standardInput = FileHandle.nullDevice
            try p.run()
        } catch {
            cleanupDownload()
            tell("Couldn't replace this copy", error.localizedDescription + " Your profile folder is not changed.")
            return
        }
        replacing = true
        NSApp.terminate(nil)
    }

    func cleanupDownload() {
        if let dir = downloadDir { try? FileManager.default.removeItem(at: dir) }
        downloadDir = nil
    }

    func profileDisplay() -> String {
        let path = profileFolder().path
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        if path.hasPrefix(home + "/") { return "~" + String(path.dropFirst(home.count)) }
        return path
    }

    func runCommand(_ launch: String, _ args: [String]) -> (status: Int32, text: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launch)
        p.arguments = args
        let out = FileManager.default.temporaryDirectory.appendingPathComponent("job-oyster-cmd-\(UUID().uuidString).log")
        FileManager.default.createFile(atPath: out.path, contents: nil)
        guard let handle = try? FileHandle(forWritingTo: out) else { return (1, "") }
        p.standardOutput = handle
        p.standardError = handle
        p.standardInput = FileHandle.nullDevice
        do { try p.run() } catch { try? handle.close(); return (1, error.localizedDescription) }
        p.waitUntilExit()
        try? handle.close()
        let text = (try? String(contentsOf: out, encoding: .utf8)) ?? ""
        try? FileManager.default.removeItem(at: out)
        return (p.terminationStatus, text)
    }

    func sayOrOpen(_ message: String, _ detail: String, page: URL?, pageButton: String = "Releases Page") {
        let a = NSAlert()
        a.messageText = message
        a.informativeText = detail
        a.addButton(withTitle: "OK")
        if page != nil { a.addButton(withTitle: pageButton) }
        if a.runModal() == .alertSecondButtonReturn, let page = page { NSWorkspace.shared.open(page) }
    }

    @objc func checkForUpdates(_ sender: Any?) { checkForUpdate(announce: true) }

    // The headlines under "## What's new" in a release's notes (the Mac app workflow lists them from
    // CHANGELOG.md), as a few lines for the update alert; "" for a release without them.
    func whatsNew(_ notes: String) -> String {
        var items: [String] = [], inside = false
        for line in notes.components(separatedBy: .newlines) {
            if line.hasPrefix("## ") { inside = line.hasPrefix("## What's new"); continue }
            if inside, line.hasPrefix("- ") { items.append(String(line.dropFirst(2))) }
        }
        guard !items.isEmpty else { return "" }
        let shown = 8
        var text = "What's new:\n" + items.prefix(shown).map { "• \($0)" }.joined(separator: "\n")
        if items.count > shown { text += "\n…and \(items.count - shown) more on the release page." }
        return text + "\n\n"
    }

    func tell(_ message: String, _ detail: String) {
        DispatchQueue.main.async {
            let a = NSAlert()
            a.messageText = message
            a.informativeText = detail
            a.addButton(withTitle: "OK")
            a.runModal()
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)     // Dock icon; menu-bar item stays too
app.run()
