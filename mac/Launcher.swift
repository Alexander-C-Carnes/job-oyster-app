// Job Oyster.app: starts the bundled web app (Python, in Contents/Resources) and opens it in the browser.
//
// The app's files are read-only inside the bundle; everything personal is in the profile folder
// (~/JobOyster, or $JOBPIPE_HOME), which the server makes on first start. Quitting the app stops the server,
// and with it any runs going (a search, Make résumé); it asks first when there are some.
// Dock icon plus a menu-bar item. The item lists runs going, recently finished, résumés ready to send, and
// roles at the Profile › Menu Bar Settings fit floor (7 by default) since yesterday; choosing one opens it in the browser.
// Clicking the Dock icon again opens the browser again. On start it checks GitHub for a newer release, and
// Check for Updates… in the menus does so any time; Install and Relaunch downloads the new DMG, checks it's
// signed by the same developer, swaps it in for this copy and opens it again. When a résumé finishes, it posts a notification; opening
// that notification shows the role.
import AppKit
import Foundation
import Security
import UserNotifications

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

let defaultPort = 8765

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, UNUserNotificationCenterDelegate {
    var server: Process?
    var url: URL?
    var logURL: URL!
    var quitting = false
    let info = Bundle.main.infoDictionary ?? [:]
    var statusItem: NSStatusItem?
    var statusMenu: NSMenu?
    var menuTimer: Timer?
    var snap = MenuSnap()
    var menuLoaded = false
    var menuError: String?          // last /api/menu failure; nil when the snap is good
    var knownRuns: [MenuSnap.RunItem] = []   // last good runs list, for "recently finished"
    var recentRuns: [(item: MenuSnap.RunItem, ended: Date)] = []
    let recentFor: TimeInterval = 45
    var updateWindow: NSWindow?
    var updateLabel: NSTextField?
    var updateBar: NSProgressIndicator?
    var updateCancel: NSButton?
    var updateDownload: UpdateDownload?
    var stagedUpdate: (app: URL, version: String)?   // checked and ready; swapped in when the app quits
    var relaunchAfterUpdate = false

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        installStatusItem()
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Job Oyster")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        logURL = logs.appendingPathComponent("server.log")
        UNUserNotificationCenter.current().delegate = self
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        start()
        offerToTrashOldApp()
        clearUpdateStage()
        checkForUpdate()
    }

    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows: Bool) -> Bool {
        openInBrowser(nil)
        return false
    }

    // Quitting stops the runs going and drops the waiting ones (the server does, on SIGINT), so say so first.
    // Only for the server this app started: quitting leaves one started some other way (`oyster run`) alone.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let s = server, s.isRunning, let going = runsGoing() else { return .terminateNow }
        let a = NSAlert()
        a.alertStyle = .warning
        a.messageText = going.prefix(1).uppercased() + going.dropFirst()
        a.informativeText = "Quitting Job Oyster stops them: résumés being made are cut short, and the ones waiting "
            + "their turn won't start. To let them finish, keep Job Oyster open."
        a.addButton(withTitle: "Quit and Stop Runs")
        a.addButton(withTitle: "Keep Running")
        if a.runModal() == .alertFirstButtonReturn { return .terminateNow }
        if let staged = stagedUpdate {
            relaunchAfterUpdate = false
            tell("Job Oyster \(staged.version) installs when you quit", "It's downloaded and checked. Quit Job Oyster "
                 + "once the runs are done and the new version takes its place; open it again from Applications.")
        }
        return .terminateCancel
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

    // MARK: résumé finished

    // Polls résumé runs that have ended and posts a notification for each new one. The first look only
    // remembers the latest id, so opening the app doesn't announce résumés that finished earlier. So does a
    // look that finds the run history started over (a new data folder), or its new runs would never show.
    let finishedRunKey = "lastFinishedRunId"
    var watchingRuns = false
    var pollBusy = false

    func watchFinishedRuns() {
        if watchingRuns { return }
        watchingRuns = true
        pollFinishedRuns()
        Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in
            self?.pollFinishedRuns()
        }
    }

    func pollFinishedRuns() {
        if pollBusy { return }
        let after = finishedAfter()
        guard let u = url, let comps = URLComponents(url: u, resolvingAgainstBaseURL: false),
              let token = comps.queryItems?.first(where: { $0.name == "token" })?.value,
              let api = URL(string: "http://127.0.0.1:\(comps.port ?? defaultPort)/api/runs/finished?after=\(after)")
        else { return }
        var req = URLRequest(url: api)
        req.timeoutInterval = 8
        req.setValue(token, forHTTPHeaderField: "X-Jobpipe-Token")
        pollBusy = true
        let seeded = UserDefaults.standard.object(forKey: finishedRunKey) != nil
        URLSession.shared.dataTask(with: req) { [weak self] data, resp, _ in
            defer { DispatchQueue.main.async { self?.pollBusy = false } }
            guard let self = self, (resp as? HTTPURLResponse)?.statusCode == 200, let data = data,
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let runs = obj["runs"] as? [[String: Any]] else { return }
            let latest = obj["latest"] as? Int ?? 0
            if !seeded || latest < after {
                UserDefaults.standard.set(latest, forKey: self.finishedRunKey)
                return
            }
            DispatchQueue.main.async { self.announceFinished(runs) }
        }.resume()
    }

    func finishedAfter() -> Int {
        UserDefaults.standard.object(forKey: finishedRunKey) == nil ? 0 : UserDefaults.standard.integer(forKey: finishedRunKey)
    }

    func announceFinished(_ runs: [[String: Any]]) {
        for run in runs {
            guard let id = run["id"] as? Int, let status = run["status"] as? String else { continue }
            let jobs = run["jobs"] as? [[String: Any]] ?? []
            let content = UNMutableNotificationContent()
            let who = self.who(jobs.first)
            if status == "done" {
                content.title = jobs.count > 1 ? "\(jobs.count) résumés ready" : "Résumé ready"
                if jobs.count > 1 {
                    content.body = who.isEmpty ? "\(jobs.count) résumés are ready to read." : "\(who), and \(jobs.count - 1) more"
                } else {
                    content.body = who.isEmpty ? "A résumé is ready to read." : who
                }
            } else {
                content.title = "Résumé didn't finish"
                content.body = who.isEmpty ? "A résumé run stopped before it finished." : who
            }
            if let job = jobs.first?["id"] as? String, !job.isEmpty { content.userInfo = ["job": job] }
            content.sound = .default
            UNUserNotificationCenter.current().add(
                UNNotificationRequest(identifier: "run-\(id)", content: content, trigger: nil))
            UserDefaults.standard.set(id, forKey: finishedRunKey)
        }
    }

    func who(_ job: [String: Any]?) -> String {
        let title = job?["title"] as? String ?? ""
        let company = job?["company"] as? String ?? ""
        if title.isEmpty { return company }
        if company.isEmpty { return title }
        return "\(title) at \(company)"
    }

    func openJob(_ id: String) { openApp(job: id, tab: "resume") }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let job = response.notification.request.content.userInfo["job"] as? String ?? ""
        DispatchQueue.main.async { self.openJob(job) }
        completionHandler()
    }

    func applicationWillTerminate(_ note: Notification) {
        quitting = true
        if let s = server, s.isRunning { s.interrupt(); s.waitUntilExit() }
        if let staged = stagedUpdate { swapInUpdate(staged.app, relaunch: relaunchAfterUpdate) }
    }

    // MARK: server

    func start() {
        let res = Bundle.main.resourceURL!
        var python = res.appendingPathComponent("python/bin/python3")
        var appDir = res.appendingPathComponent("app")
        let port = Int(ProcessInfo.processInfo.environment["OYSTER_PORT"] ?? ProcessInfo.processInfo.environment["JOBSEARCH_PORT"] ?? "") ?? defaultPort

        // Already running (another copy of the app, or `oyster run`). One too old for the menu bar still opens;
        // the menu says it's too old.
        if answers(port: port) {
            waitForURL(fromLog: nil, port: port)
            return
        }
        // Full builds ship python + app under Resources. A launcher-only bundle in a checkout's build/ folder
        // (for trying the launcher while developing) has neither: use the checkout's .venv.
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
                    DispatchQueue.main.async {
                        self.url = found; self.openInBrowser(nil); self.fetchMenu(); self.watchFinishedRuns()
                    }
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
        return URL(string: "http://127.0.0.1:\(port)/?token=\(menuToken(port: port))")
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
            button.imagePosition = .imageLeft
        }
        statusItem = item
        statusMenu = menu
        applyStatusButton()
        scheduleMenuPoll()
    }

    // The app icon's Side Clam (mac/make_icon.swift) as a one-colour template, 16pt tall: the cup, the lid swung
    // open on the hinge, and the pearl with a gap around it so it stands apart from the shell without its colour.
    func menuBarIcon() -> NSImage {
        func bowl(_ y: CGFloat, bulge: CGFloat) -> NSBezierPath {
            let p = NSBezierPath()
            p.move(to: NSPoint(x: 250, y: y))
            p.curve(to: NSPoint(x: 800, y: y),
                    controlPoint1: NSPoint(x: 250 + 2.0 / 3 * 270, y: y + 2.0 / 3 * bulge),
                    controlPoint2: NSPoint(x: 800 - 2.0 / 3 * 280, y: y + 2.0 / 3 * bulge))
            p.close()
            return p
        }
        let cup = bowl(560, bulge: 270)
        let lid = bowl(536, bulge: -270)
        var hinge = AffineTransform(translationByX: 250, byY: 548)
        hinge.rotate(byDegrees: -24)
        hinge.translate(x: -250, y: -548)
        lid.transform(using: hinge)
        let pearl = NSBezierPath(ovalIn: NSRect(x: 540, y: 450, width: 120, height: 120))
        let gap = NSBezierPath(ovalIn: NSRect(x: 510, y: 420, width: 180, height: 180))
        let box = cup.bounds.union(lid.bounds).union(pearl.bounds)
        let scale = 16 / box.height
        let img = NSImage(size: NSSize(width: (box.width * scale).rounded(.up), height: 16), flipped: true) { _ in
            let t = NSAffineTransform()
            t.scale(by: scale)
            t.translateX(by: -box.minX, yBy: -box.minY)
            t.concat()
            NSColor.black.setFill()
            cup.fill()
            lid.fill()
            NSGraphicsContext.current?.compositingOperation = .clear
            gap.fill()
            NSGraphicsContext.current?.compositingOperation = .sourceOver
            pearl.fill()
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
        addHeader("Ready to send", to: menu)
        addRoles(snap.ready, tab: "resume", empty: "Nothing ready to send", to: menu)
        menu.addItem(.separator())
        addHeader("Newest at fit score \(snap.fitFloor)+ since yesterday", to: menu)
        addRoles(snap.newRoles, tab: "posting", empty: "No roles at fit score \(snap.fitFloor)+ since yesterday", showFit: true, to: menu)
        menu.addItem(.separator())
        let check = NSMenuItem(title: "Check for Updates…", action: #selector(checkForUpdates(_:)), keyEquivalent: "")
        check.target = self
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

    // The page, or a role in it through #job=<id>&tab=<tab> (notifications use it too); #tab=runs opens Logs.
    func openApp(job: String = "", tab: String = "") {
        guard let u = url else { return }
        var hash: [String] = []
        if !job.isEmpty, let id = job.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) { hash.append("job=\(id)") }
        if !tab.isEmpty { hash.append("tab=\(tab)") }
        if let page = hash.isEmpty ? u : URL(string: u.absoluteString + "#" + hash.joined(separator: "&")) {
            NSWorkspace.shared.open(page)
        }
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
        m.addItem(menuItem("Check for Updates…", #selector(checkForUpdates(_:))))
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
        dock.addItem(menuItem("Check for Updates…", #selector(checkForUpdates(_:))))
        dockMenu = dock
    }

    func menuItem(_ title: String, _ action: Selector, key: String = "", target: AnyObject? = nil) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = target ?? self
        return item
    }

    var dockMenu: NSMenu?
    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? { dockMenu }

    // MARK: updates

    // Compares this version with the newest GitHub release of JSUpdateRepo ("owner/repo") and offers it.
    // On start it's quiet unless there is one. Check for Updates… (announce) also says when there isn't, when
    // GitHub can't be reached, and that a dev build (0.0.0-dev, from a branch rather than a release tag) isn't
    // updated this way: whoever built one has the source.
    func checkForUpdate(announce: Bool = false) {
        let version = info["CFBundleShortVersionString"] as? String ?? ""
        guard let repo = info["JSUpdateRepo"] as? String, !repo.isEmpty else {
            if announce {
                tell("This copy can't check for updates", "It was built without a place to check. The newest "
                     + "version is on the Job Oyster GitHub page.")
            }
            return
        }
        guard !version.isEmpty, !version.contains("-"),
              let api = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else {
            if announce {
                tell("Job Oyster \(version) is a development build", "It was built from the code rather than a "
                     + "release, so it doesn't update from GitHub. Pull the latest code and build it again.")
            }
            return
        }
        let current = version
        var req = URLRequest(url: api)
        req.timeoutInterval = 15
        URLSession.shared.dataTask(with: req) { data, _, error in
            guard let data = data,
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tag = obj["tag_name"] as? String,
                  let page = (obj["html_url"] as? String).flatMap(URL.init(string:)) else {
                guard announce else { return }
                DispatchQueue.main.async {
                    let a = NSAlert()
                    a.messageText = "Couldn't check for updates"
                    a.informativeText = "Job Oyster couldn't reach GitHub"
                        + (error.map { ": \($0.localizedDescription)" } ?? ".")
                        + " Check your connection and try again, or look at the releases page yourself."
                    a.addButton(withTitle: "OK")
                    a.addButton(withTitle: "Releases Page")
                    if a.runModal() == .alertSecondButtonReturn,
                       let releases = URL(string: "https://github.com/\(repo)/releases") {
                        NSWorkspace.shared.open(releases)
                    }
                }
                return
            }
            let latest = tag.trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
            guard latest.compare(current, options: .numeric) == .orderedDescending else {
                if announce { self.tell("You're on the latest version", "Job Oyster \(current) is the newest release.") }
                return
            }
            let news = self.whatsNew(obj["body"] as? String ?? "")
            let asset = self.dmgAsset(obj["assets"], version: latest)
            DispatchQueue.main.async { self.offerUpdate(latest, current: current, news: news, asset: asset, page: page) }
        }.resume()
    }

    @objc func checkForUpdates(_ sender: Any?) {
        if let w = updateWindow { w.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        if let staged = stagedUpdate { offerRelaunch(staged.version); return }
        checkForUpdate(announce: true)
    }

    // A newer release is out. Install and Relaunch when this copy can replace itself; otherwise the release page,
    // to download and drag in by hand.
    func offerUpdate(_ latest: String, current: String, news: String, asset: (url: URL, size: Int)?, page: URL) {
        let blocker = asset == nil ? "This release has no disk image for this Mac yet." : installBlocker()
        let a = NSAlert()
        a.messageText = "Job Oyster \(latest) is available"
        if let why = blocker {
            a.informativeText = news + "You have \(current). Download the new version, quit this one, and drag the "
                + "new one into Applications. Your profile folder and everything in it stay as they are.\n\n"
                + "Why not in one click: \(why)"
            a.addButton(withTitle: "Download")
            a.addButton(withTitle: "Later")
            if a.runModal() == .alertFirstButtonReturn { NSWorkspace.shared.open(page) }
            return
        }
        guard let asset = asset else { return }
        let mb = max(1, asset.size / 1_000_000)
        a.informativeText = news + "You have \(current). Install and Relaunch downloads it (about \(mb) MB), checks "
            + "it's signed by the same developer, replaces this copy and opens it again. Your profile folder and "
            + "everything in it stay as they are."
        a.addButton(withTitle: "Install and Relaunch")
        a.addButton(withTitle: "Later")
        a.addButton(withTitle: "Release Page")
        switch a.runModal() {
        case .alertFirstButtonReturn: downloadUpdate(asset, version: latest, page: page)
        case .alertThirdButtonReturn: NSWorkspace.shared.open(page)
        default: break
        }
    }

    // The release's DMG for this Mac: Job-Oyster-<version>-<arch>.dmg, else the stable Job-Oyster-<arch>.dmg.
    func dmgAsset(_ raw: Any?, version: String) -> (url: URL, size: Int)? {
        #if arch(arm64)
        let arch = "arm64"
        #else
        let arch = "x86_64"
        #endif
        let assets = raw as? [[String: Any]] ?? []
        for name in ["Job-Oyster-\(version)-\(arch).dmg", "Job-Oyster-\(arch).dmg"] {
            if let a = assets.first(where: { $0["name"] as? String == name }),
               let u = (a["browser_download_url"] as? String).flatMap(URL.init(string:)) {
                return (u, a["size"] as? Int ?? 0)
            }
        }
        return nil
    }

    // Why this copy can't replace itself in place, or nil when it can.
    func installBlocker() -> String? {
        let bundle = Bundle.main.bundleURL
        let folder = bundle.deletingLastPathComponent()
        if bundle.path.contains("/AppTranslocation/") {
            return "macOS is running this copy from a temporary place because it hasn't been moved since it was "
                + "downloaded. Drag Job Oyster into Applications, open it from there, and updates install in one click."
        }
        if (try? bundle.resourceValues(forKeys: [.volumeIsReadOnlyKey]))?.volumeIsReadOnly == true {
            return "Job Oyster is running from its disk image. Drag it into Applications, open it from there, and "
                + "updates install in one click."
        }
        if !FileManager.default.isWritableFile(atPath: folder.path) {
            return "this Mac account can't change \(folder.path), where Job Oyster is."
        }
        if teamID(of: bundle) == nil {
            return "this copy isn't signed with a Developer ID, so there's nothing to check the new one against."
        }
        return nil
    }

    // The Developer ID team that signed the app at url, or nil for an ad-hoc or unsigned one.
    func teamID(of url: URL) -> String? {
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(url as CFURL, [], &code) == errSecSuccess, let c = code else { return nil }
        var info: CFDictionary?
        guard SecCodeCopySigningInformation(c, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess,
              let d = info as? [String: Any] else { return nil }
        return d[kSecCodeInfoTeamIdentifier as String] as? String
    }

    // Nil when the app at url is the release we asked for: same bundle id, the expected version, and a valid
    // signature (nested code included) from an Apple-issued certificate for this copy's team.
    func checkUpdate(_ url: URL, version: String) -> String? {
        guard let team = teamID(of: Bundle.main.bundleURL), let id = info["CFBundleIdentifier"] as? String else {
            return "This copy isn't signed with a Developer ID, so there's nothing to check the new one against."
        }
        let newInfo = NSDictionary(contentsOf: url.appendingPathComponent("Contents/Info.plist")) as? [String: Any] ?? [:]
        guard newInfo["CFBundleIdentifier"] as? String == id else { return "The download isn't Job Oyster." }
        let got = newInfo["CFBundleShortVersionString"] as? String ?? "?"
        guard got == version else { return "The download is version \(got), not \(version)." }
        var code: SecStaticCode?, req: SecRequirement?
        let text = "anchor apple generic and identifier \"\(id)\" and certificate leaf[subject.OU] = \"\(team)\""
        guard SecStaticCodeCreateWithPath(url as CFURL, [], &code) == errSecSuccess, let c = code,
              SecRequirementCreateWithString(text as CFString, [], &req) == errSecSuccess else {
            return "Its signature couldn't be read."
        }
        let flags = SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSCheckNestedCode | kSecCSStrictValidate)
        let status = SecStaticCodeCheckValidity(c, flags, req)
        guard status == errSecSuccess else {
            let why = SecCopyErrorMessageString(status, nil) as String? ?? "error \(status)"
            return "Its signature didn't check out (\(why)), so it wasn't installed."
        }
        return nil
    }

    // The staging folder beside this copy (same volume, so the swap is a rename).
    func updateStage() -> URL {
        Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent(".Job Oyster update")
    }

    // A stage left by an update that didn't finish (or the old copy from one that did).
    func clearUpdateStage() {
        let stage = updateStage()
        DispatchQueue.global(qos: .utility).async { try? FileManager.default.removeItem(at: stage) }
    }

    func downloadUpdate(_ asset: (url: URL, size: Int), version: String, page: URL) {
        showUpdateWindow("Downloading Job Oyster \(version)…")
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        let dest = caches.appendingPathComponent("Job Oyster/\(asset.url.lastPathComponent)")
        let mb = Double(max(asset.size, 1)) / 1_000_000
        let d = UpdateDownload(dest: dest, progress: { [weak self] f in
            self?.updateBar?.isIndeterminate = false
            self?.updateBar?.doubleValue = f
            self?.updateLabel?.stringValue = String(format: "Downloading Job Oyster %@… %.0f of %.0f MB", version, f * mb, mb)
        }, done: { [weak self] result in
            guard let self = self else { return }
            self.updateDownload = nil
            switch result {
            case .failure(let e as NSError) where e.domain == NSURLErrorDomain && e.code == NSURLErrorCancelled:
                self.closeUpdateWindow()
            case .failure(let e):
                self.updateFailed("The download didn't finish: \(e.localizedDescription)", page: page)
            case .success(let dmg):
                self.installUpdate(dmg, version: version, page: page)
            }
        })
        updateDownload = d
        d.start(asset.url)
    }

    // Opens the DMG, copies the app beside this one, checks it, then quits; the swap runs once this process is gone.
    func installUpdate(_ dmg: URL, version: String, page: URL) {
        updateLabel?.stringValue = "Checking Job Oyster \(version)…"
        updateBar?.isIndeterminate = true
        updateBar?.startAnimation(nil)
        updateCancel?.isEnabled = false
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Result { try self.stageUpdate(dmg, version: version) }
            try? FileManager.default.removeItem(at: dmg)
            DispatchQueue.main.async {
                switch result {
                case .failure(let e): self.updateFailed(e.localizedDescription, page: page)
                case .success(let app):
                    self.stagedUpdate = (app, version)
                    self.relaunchAfterUpdate = true
                    self.updateLabel?.stringValue = "Restarting in Job Oyster \(version)…"
                    self.closeUpdateWindow()
                    NSApp.terminate(nil)
                }
            }
        }
    }

    func stageUpdate(_ dmg: URL, version: String) throws -> URL {
        let fm = FileManager.default
        let mount = fm.temporaryDirectory.appendingPathComponent("JobOysterUpdate-\(UUID().uuidString)")
        try fm.createDirectory(at: mount, withIntermediateDirectories: true)
        let (attached, out) = shell("/usr/bin/hdiutil", ["attach", dmg.path, "-nobrowse", "-readonly", "-noautoopen",
                                                         "-mountpoint", mount.path])
        guard attached == 0 else { throw UpdateError("macOS couldn't open the downloaded disk image. \(out)") }
        defer {
            if shell("/usr/bin/hdiutil", ["detach", mount.path]).0 != 0 {
                _ = shell("/usr/bin/hdiutil", ["detach", mount.path, "-force"])
            }
            try? fm.removeItem(at: mount)
        }
        let source = mount.appendingPathComponent("Job Oyster.app")
        guard fm.fileExists(atPath: source.path) else { throw UpdateError("The disk image has no Job Oyster.app in it.") }
        let stage = updateStage()
        try? fm.removeItem(at: stage)
        try fm.createDirectory(at: stage, withIntermediateDirectories: true)
        let staged = stage.appendingPathComponent("Job Oyster.app")
        let (copied, copyOut) = shell("/usr/bin/ditto", [source.path, staged.path])
        guard copied == 0 else {
            try? fm.removeItem(at: stage)
            throw UpdateError("Copying the new version failed. \(copyOut)")
        }
        if let problem = checkUpdate(staged, version: version) {
            try? fm.removeItem(at: stage)
            throw UpdateError(problem)
        }
        return staged
    }

    // Runs after this process exits: old copy aside, new one in its place, open it, then clear the stage. If the
    // move fails the old copy goes back. Logged to update.log beside server.log.
    func swapInUpdate(_ staged: URL, relaunch: Bool) {
        let script = """
            pid=$1; new=$2; target=$3; relaunch=$4; stage=$(dirname "$new")
            echo "$(date '+%F %T') installing $new over $target"
            n=0; while kill -0 "$pid" 2>/dev/null; do n=$((n+1)); [ $n -gt 600 ] && { echo "app didn't quit"; exit 1; }; sleep 0.1; done
            rm -rf "$stage/old.app"
            if mv "$target" "$stage/old.app"; then
              if mv "$new" "$target"; then echo "installed"; else echo "swap failed; old copy put back"; mv "$stage/old.app" "$target"; fi
            else echo "couldn't move the old copy aside"; fi
            [ "$relaunch" = 1 ] && open "$target"
            rm -rf "$stage"
            """
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", script, "sh", String(getpid()), staged.path, Bundle.main.bundleURL.path, relaunch ? "1" : "0"]
        let log = logURL.deletingLastPathComponent().appendingPathComponent("update.log")
        if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil) }
        if let h = try? FileHandle(forWritingTo: log) { h.seekToEndOfFile(); p.standardOutput = h; p.standardError = h }
        try? p.run()
    }

    func offerRelaunch(_ version: String) {
        let a = NSAlert()
        a.messageText = "Job Oyster \(version) is ready to install"
        a.informativeText = "It's downloaded and checked. Relaunch to switch to it."
        a.addButton(withTitle: "Relaunch Now")
        a.addButton(withTitle: "Later")
        if a.runModal() == .alertFirstButtonReturn { relaunchAfterUpdate = true; NSApp.terminate(nil) }
    }

    func shell(_ tool: String, _ args: [String]) -> (Int32, String) {
        let p = Process(), pipe = Pipe()
        p.executableURL = URL(fileURLWithPath: tool)
        p.arguments = args
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch { return (-1, error.localizedDescription) }
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        p.waitUntilExit()
        return (p.terminationStatus, out.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func showUpdateWindow(_ text: String) {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 118), styleMask: [.titled],
                         backing: .buffered, defer: false)
        w.title = "Updating Job Oyster"
        w.isReleasedWhenClosed = false
        let label = NSTextField(labelWithString: text)
        label.frame = NSRect(x: 20, y: 78, width: 380, height: 20)
        let bar = NSProgressIndicator(frame: NSRect(x: 20, y: 52, width: 380, height: 20))
        bar.style = .bar
        bar.minValue = 0
        bar.maxValue = 1
        bar.isIndeterminate = true
        bar.startAnimation(nil)
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancelUpdate(_:)))
        cancel.frame = NSRect(x: 310, y: 12, width: 96, height: 30)
        cancel.keyEquivalent = "\u{1b}"
        for v in [label, bar, cancel] as [NSView] { w.contentView?.addSubview(v) }
        w.center()
        updateWindow = w; updateLabel = label; updateBar = bar; updateCancel = cancel
        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
    }

    func closeUpdateWindow() {
        updateWindow?.orderOut(nil)
        updateWindow = nil; updateLabel = nil; updateBar = nil; updateCancel = nil
    }

    @objc func cancelUpdate(_ sender: Any?) { updateDownload?.cancel() }

    func updateFailed(_ why: String, page: URL) {
        closeUpdateWindow()
        let a = NSAlert()
        a.messageText = "Couldn't install the update"
        a.informativeText = why + "\n\nThis copy is unchanged. You can download the new version from the release page "
            + "and drag it into Applications instead."
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Release Page")
        if a.runModal() == .alertSecondButtonReturn { NSWorkspace.shared.open(page) }
    }

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

struct UpdateError: LocalizedError {
    let errorDescription: String?
    init(_ text: String) { errorDescription = text }
}

/// Downloads a release DMG to dest, reporting progress (0…1) and the result on the main queue.
final class UpdateDownload: NSObject, URLSessionDownloadDelegate {
    let dest: URL
    let progress: (Double) -> Void
    let done: (Result<URL, Error>) -> Void
    var task: URLSessionDownloadTask?
    var saved: Result<URL, Error>?

    init(dest: URL, progress: @escaping (Double) -> Void, done: @escaping (Result<URL, Error>) -> Void) {
        self.dest = dest; self.progress = progress; self.done = done
    }

    func start(_ url: URL) {
        let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        task = session.downloadTask(with: url)
        task?.resume()
    }

    func cancel() { task?.cancel() }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData _: Int64,
                    totalBytesWritten written: Int64, totalBytesExpectedToWrite total: Int64) {
        guard total > 0 else { return }
        let f = Double(written) / Double(total)
        DispatchQueue.main.async { self.progress(f) }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        let status = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 0
        saved = Result {
            guard status == 200 else { throw UpdateError("GitHub answered \(status).") }
            let fm = FileManager.default
            try fm.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? fm.removeItem(at: dest)
            try fm.moveItem(at: location, to: dest)
            return dest
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let result: Result<URL, Error> = error.map { .failure($0) } ?? saved ?? .failure(UpdateError("Nothing was downloaded."))
        session.finishTasksAndInvalidate()
        DispatchQueue.main.async { self.done(result) }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)     // Dock icon; menu-bar item stays too
app.run()
