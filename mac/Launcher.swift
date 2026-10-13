// Job Search.app: starts the bundled web app (Python, in Contents/Resources) and opens it in the browser.
//
// The app's files are read-only inside the bundle; everything personal is in the profile folder
// (~/JobSearch, or $JOBPIPE_HOME), which the server makes on first start. Quitting the app stops the server,
// and with it any runs going (a search, Make résumé); it asks first when there are some.
// Clicking the Dock icon again opens the browser again. On start it checks GitHub for a newer release, and
// Check for Updates… in the menu does so any time.
import AppKit
import Foundation

let defaultPort = 8765

final class AppDelegate: NSObject, NSApplicationDelegate {
    var server: Process?
    var url: URL?
    var logURL: URL!
    var quitting = false
    let info = Bundle.main.infoDictionary ?? [:]

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Job Search")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        logURL = logs.appendingPathComponent("server.log")
        start()
        checkForUpdate()
    }

    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows: Bool) -> Bool {
        openInBrowser(nil)
        return false
    }

    // Quitting stops the runs going and drops the waiting ones (the server does, on SIGINT), so say so first.
    // Only for the server this app started: quitting leaves one started some other way (`jobsearch run`) alone.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let s = server, s.isRunning, let going = runsGoing() else { return .terminateNow }
        let a = NSAlert()
        a.alertStyle = .warning
        a.messageText = going.prefix(1).uppercased() + going.dropFirst()
        a.informativeText = "Quitting Job Search stops them: résumés being made are cut short, and the ones waiting "
            + "their turn won't start. To let them finish, keep Job Search open."
        a.addButton(withTitle: "Quit and Stop Runs")
        a.addButton(withTitle: "Keep Running")
        return a.runModal() == .alertFirstButtonReturn ? .terminateNow : .terminateCancel
    }

    // "2 runs are going and 1 is waiting their turn", or nil when none are (or the server doesn't answer).
    func runsGoing() -> String? {
        guard let u = url, let comps = URLComponents(url: u, resolvingAgainstBaseURL: false),
              let token = comps.queryItems?.first(where: { $0.name == "token" })?.value,
              let api = URL(string: "http://127.0.0.1:\(comps.port ?? defaultPort)/api/runs/going") else { return nil }
        var req = URLRequest(url: api)
        req.timeoutInterval = 3
        req.setValue(token, forHTTPHeaderField: "X-Jobpipe-Token")
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
        let python = res.appendingPathComponent("python/bin/python3")
        let port = Int(ProcessInfo.processInfo.environment["JOBSEARCH_PORT"] ?? "") ?? defaultPort

        if answers(port: port) {                     // already running (another copy of the app, or `jobsearch run`)
            waitForURL(fromLog: nil, port: port)
            return
        }
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let log = try! FileHandle(forWritingTo: logURL)

        let p = Process()
        p.executableURL = python
        p.arguments = ["-m", "jobpipe", "serve", "--port", String(port), "--no-open"]
        p.currentDirectoryURL = res.appendingPathComponent("app")
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        // Apps start with a bare PATH; add where Claude Code and Homebrew live.
        env["PATH"] = ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "\(home)/.npm-global/bin",
                       env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"].joined(separator: ":")
        env["PYTHONDONTWRITEBYTECODE"] = "1"       // the bundle is read-only (and signed)
        env["PYTHONUNBUFFERED"] = "1"
        env["PLAYWRIGHT_BROWSERS_PATH"] = res.appendingPathComponent("ms-playwright").path
        env["JOBSEARCH_APP_VERSION"] = info["CFBundleShortVersionString"] as? String ?? ""
        p.environment = env
        p.standardOutput = log
        p.standardError = log
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.serverStopped(proc.terminationStatus) }
        }
        do { try p.run() } catch {
            fail("Job Search couldn't start its server: \(error.localizedDescription)")
            return
        }
        server = p
        waitForURL(fromLog: logURL, port: port)
    }

    // The server prints "running at http://127.0.0.1:PORT/?token=…" once it's up; open that.
    func waitForURL(fromLog log: URL?, port: Int) {
        let deadline = Date().addingTimeInterval(120)
        DispatchQueue.global().async { [weak self] in
            guard let self = self else { return }
            while Date() < deadline {
                if let found = self.urlFromLog(log, port: port) {
                    DispatchQueue.main.async { self.url = found; self.openInBrowser(nil) }
                    return
                }
                if let s = self.server, !s.isRunning { return }
                Thread.sleep(forTimeInterval: 0.4)
            }
            DispatchQueue.main.async { self.fail("Job Search didn't start within two minutes.") }
        }
    }

    func urlFromLog(_ log: URL?, port: Int) -> URL? {
        if let log = log {
            guard let text = try? String(contentsOf: log, encoding: .utf8),
                  let r = text.range(of: #"http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_\-]+"#, options: .regularExpression)
            else { return nil }
            return URL(string: String(text[r]))
        }
        // Someone else's server: its token is in the profile's data folder.
        let env = ProcessInfo.processInfo.environment
        let home = env["JOBPIPE_HOME"].map { ($0 as NSString).expandingTildeInPath }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("JobSearch").path
        let data = env["JOBPIPE_DATA_DIR"].map { ($0 as NSString).expandingTildeInPath } ?? home + "/data"
        let token = (try? String(contentsOfFile: data + "/web-token", encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
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

    func serverStopped(_ status: Int32) {
        if quitting { return }
        fail("Job Search stopped unexpectedly (exit \(status)).")
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

    // MARK: menu actions

    @objc func openInBrowser(_ sender: Any?) {
        if let u = url { NSWorkspace.shared.open(u) }
    }

    @objc func showProfile(_ sender: Any?) {
        let env = ProcessInfo.processInfo.environment
        let home = env["JOBPIPE_HOME"].map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("JobSearch")
        NSWorkspace.shared.open(home)
    }

    @objc func showLog(_ sender: Any?) {
        NSWorkspace.shared.activateFileViewerSelecting([logURL])
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let m = NSMenu()
        m.addItem(withTitle: "Open Job Search", action: #selector(openInBrowser(_:)), keyEquivalent: "o")
        m.addItem(withTitle: "Check for Updates…", action: #selector(checkForUpdates(_:)), keyEquivalent: "")
        m.addItem(.separator())
        m.addItem(withTitle: "Show Profile Folder", action: #selector(showProfile(_:)), keyEquivalent: "")
        m.addItem(withTitle: "Show Log", action: #selector(showLog(_:)), keyEquivalent: "")
        m.addItem(.separator())
        m.addItem(withTitle: "Quit Job Search", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = m
        NSApp.mainMenu = main
        let dock = NSMenu()
        dock.addItem(withTitle: "Open Job Search", action: #selector(openInBrowser(_:)), keyEquivalent: "")
        dock.addItem(withTitle: "Show Profile Folder", action: #selector(showProfile(_:)), keyEquivalent: "")
        dock.addItem(withTitle: "Check for Updates…", action: #selector(checkForUpdates(_:)), keyEquivalent: "")
        dockMenu = dock
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
                     + "version is on the Job Search GitHub page.")
            }
            return
        }
        guard !version.isEmpty, !version.contains("-"),
              let api = URL(string: "https://api.github.com/repos/\(repo)/releases/latest") else {
            if announce {
                tell("Job Search \(version) is a development build", "It was built from the code rather than a "
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
                    a.informativeText = "Job Search couldn't reach GitHub"
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
                if announce { self.tell("You're on the latest version", "Job Search \(current) is the newest release.") }
                return
            }
            DispatchQueue.main.async {
                let a = NSAlert()
                a.messageText = "Job Search \(latest) is available"
                a.informativeText = "You have \(current). Download the new version, quit this one, and drag the new one "
                    + "into Applications. Your profile folder and everything in it stay as they are."
                a.addButton(withTitle: "Download")
                a.addButton(withTitle: "Later")
                if a.runModal() == .alertFirstButtonReturn { NSWorkspace.shared.open(page) }
            }
        }.resume()
    }

    @objc func checkForUpdates(_ sender: Any?) { checkForUpdate(announce: true) }

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
app.setActivationPolicy(.regular)
app.run()
