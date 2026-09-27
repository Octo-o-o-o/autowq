import AppKit
import Darwin
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var item: NSStatusItem!
    let menu = NSMenu()
    // 界面语言：初值按系统检测（zh* → 中文，其余英文）；settings 加载后以 config 的 ui.language 为准。
    var lang: String = Locale.preferredLanguages.first?.hasPrefix("zh") == true ? "zh" : "en"
    var historyMenu = NSMenu()
    var submissionsMenu = NSMenu()
    var settingsMenu = NSMenu()
    var accountMenu = NSMenu()
    var presetMenu = NSMenu()
    var providerMenu = NSMenu()
    var intervalMenu = NSMenu()
    var dailyMenu = NSMenu()
    var totalMenu = NSMenu()
    var languageMenu = NSMenu()
    var timer: Timer?
    var notifyTimer: Timer?
    var actionBusy = false
    var loading = Set<String>()
    var fetched: [String: Date] = [:]
    var lockFD: Int32 = -1
    var ready = false
    var root: String { UserDefaults.standard.string(forKey: "WQWorkspace") ?? "" }
    var python: String { UserDefaults.standard.string(forKey: "WQPython") ?? "" }
    var configured: Bool { !root.isEmpty && !python.isEmpty }
    let activateRow = NSMenuItem(title: "", action: #selector(activate), keyEquivalent: "")
    let activateSep = NSMenuItem.separator()
    let headline = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let detail = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let cycles = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let submissions = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let tick = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let nextAt = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let researchModel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let reviewModel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let brainRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let notifyRow = NSMenuItem(title: "", action: #selector(apply(_:)), keyEquivalent: "")
    var controls: [NSMenuItem] = []

    func t(_ zh: String, _ en: String) -> String { lang == "zh" ? zh : en }

    func textRow(_ row: NSMenuItem, _ value: String) {
        row.title = value
        row.attributedTitle = NSAttributedString(string: value, attributes: [.foregroundColor: NSColor.labelColor])
        row.toolTip = value
        row.isEnabled = false
    }
    func info(_ text: String) -> NSMenuItem {
        let row = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        textRow(row, text); return row
    }
    func block(_ lines: [String], width: CGFloat = 560, titleColor: NSColor? = nil) -> NSMenuItem {
        let text = NSTextField(wrappingLabelWithString: "")
        let attr = NSMutableAttributedString()
        if let title = lines.first {
            attr.append(NSAttributedString(string: title, attributes: [
                .font: NSFont.systemFont(ofSize: 13, weight: .semibold),
                .foregroundColor: titleColor ?? NSColor.labelColor]))
        }
        let rest = lines.dropFirst()
        if !rest.isEmpty {
            if !attr.string.isEmpty { attr.append(NSAttributedString(string: "\n")) }
            attr.append(NSAttributedString(string: rest.joined(separator: "\n"), attributes: [
                .font: NSFont.systemFont(ofSize: 12),
                .foregroundColor: NSColor.secondaryLabelColor]))
        }
        text.attributedStringValue = attr
        text.preferredMaxLayoutWidth = width - 28
        text.frame = NSRect(x: 14, y: 8, width: width - 28, height: 1)
        let height = text.cell!.cellSize(forBounds: NSRect(x: 0, y: 0, width: width - 28, height: 10000)).height
        text.frame.size.height = height
        let view = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height + 16))
        view.addSubview(text)
        view.setAccessibilityElement(true)
        view.setAccessibilityLabel(lines.joined(separator: "，"))
        let row = NSMenuItem(title: lines.first ?? "", action: nil, keyEquivalent: "")
        row.view = view; row.isEnabled = false
        return row
    }
    func alert(_ title: String, _ text: String) {
        let box = NSAlert(); box.messageText = title; box.informativeText = text; box.runModal()
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        let bundlePath = Bundle.main.bundlePath
        if bundlePath.hasPrefix("/Volumes/") || bundlePath.contains("/AppTranslocation/") {
            alert(t("请先安装到「应用程序」", "Install to /Applications first"),
                  t("WorldQuant 不能从磁盘映像或临时位置运行。\n请把 WorldQuant.app 拖入「应用程序」文件夹，再从那里启动。",
                    "WorldQuant cannot run from a disk image or a translocated location.\nDrag WorldQuant.app into the Applications folder and launch it from there."))
            NSApp.terminate(nil); return
        }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let path = Bundle.main.path(forResource: "ResearchIcon", ofType: "png"), let icon = NSImage(contentsOfFile: path) {
            icon.size = NSSize(width: 22, height: 22); icon.isTemplate = true
            item.button?.image = icon
        } else { item.button?.title = "WQ" }
        item.button?.setAccessibilityLabel("WorldQuant")
        menu.autoenablesItems = false
        menu.delegate = self
        item.menu = menu
        if configured {
            menu.addItem(info(t("正在检查内置引擎更新…", "Checking bundled engine updates…")))
            runSetup(["setup", "--workspace", root]) { result in
                if let error = result["error"] as? String {
                    self.alert(self.t("引擎更新未完成", "Engine update incomplete"), error)
                    NSApp.terminate(nil)
                    return
                }
                if let python = result["python"] as? String {
                    UserDefaults.standard.set(python, forKey: "WQPython")
                }
                self.enterMainMode()
            }
        } else { buildFirstRunMenu() }
    }
    func buildFirstRunMenu() {
        menu.removeAllItems()
        menu.addItem(info(t("尚未配置工作区", "No workspace configured")))
        menu.addItem(.separator())
        for (title, selector, tip) in [
            (t("首次设置…", "First-time setup…"), #selector(firstSetup),
             t("选择或新建工作区目录，安装内置引擎运行时，并在终端完成初始向导。",
               "Choose or create a workspace, install the bundled engine runtime, and finish the wizard in Terminal.")),
            (t("选择已有工作区…", "Choose an existing workspace…"), #selector(chooseWorkspace),
             t("关联一个已包含 config/config.json 的工作区目录。",
               "Link a workspace directory that already contains config/config.json."))
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; menu.addItem(row)
        }
        menu.addItem(.separator())
        let quitRow = NSMenuItem(title: t("退出", "Quit"), action: #selector(quitFirstRun), keyEquivalent: "")
        quitRow.target = self; menu.addItem(quitRow)
    }
    @objc func quitFirstRun() { NSApp.terminate(nil) }
    func pickWorkspace(create: Bool) -> String? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false; panel.canChooseDirectories = true
        panel.canCreateDirectories = true; panel.allowsMultipleSelection = false
        panel.prompt = t("选择", "Choose")
        let fallback = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("autowq")
        panel.directoryURL = FileManager.default.fileExists(atPath: fallback.path) ? fallback
            : FileManager.default.homeDirectoryForCurrentUser
        panel.message = create ? t("选择或新建一个目录作为工作区（建议 ~/autowq）", "Choose or create a workspace directory (~/autowq recommended)")
            : t("选择一个已包含 config/config.json 的工作区目录", "Choose a directory that already contains config/config.json")
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return url.path
    }
    func runSetup(_ arguments: [String], _ completion: @escaping ([String: Any]) -> Void) {
        guard let script = Bundle.main.path(forResource: "app_setup", ofType: "py") else {
            completion(["error": t("应用包缺少 app_setup.py", "app_setup.py missing from the app bundle")]); return
        }
        item.button?.appearsDisabled = true
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [script] + arguments
            let pipe = Pipe(); process.standardOutput = pipe; process.standardError = pipe
            var result: [String: Any]
            do {
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                result = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
                if process.terminationStatus != 0 && result["error"] == nil {
                    result["error"] = String(format: self.t("安装脚本失败（退出码 %d）", "Setup script failed (exit code %d)"), process.terminationStatus)
                }
            } catch { result = ["error": error.localizedDescription] }
            DispatchQueue.main.async {
                self.item.button?.appearsDisabled = false
                completion(result)
            }
        }
    }
    func finishSetup(_ result: [String: Any], onboard: Bool) {
        if let error = result["error"] as? String { alert(t("设置未完成", "Setup incomplete"), error); return }
        guard let workspace = result["workspace"] as? String, let python = result["python"] as? String else {
            alert(t("设置未完成", "Setup incomplete"), t("安装脚本返回了无法识别的结果。", "The setup script returned an unrecognized result.")); return
        }
        UserDefaults.standard.set(workspace, forKey: "WQWorkspace")
        UserDefaults.standard.set(python, forKey: "WQPython")
        if onboard { openOnboard(workspace, python) }
        alert(t("运行时已就绪", "Runtime ready"), onboard
            ? t("已在 Terminal 中打开 onboard 向导。\n完成后回到本菜单，在「设置」中选择「启用调度与自启」。",
                "The onboarding wizard opened in Terminal.\nWhen finished, choose “Enable scheduling & auto-start” in Settings.")
            : t("工作区已关联。如需开机自启与定时调度，在「设置」中选择「启用调度与自启」。",
                "Workspace linked. To enable auto-start and scheduled runs, choose “Enable scheduling & auto-start” in Settings."))
        enterMainMode()
    }
    @objc func firstSetup() {
        guard let path = pickWorkspace(create: true) else { return }
        runSetup(["setup", "--workspace", path]) { self.finishSetup($0, onboard: true) }
    }
    @objc func chooseWorkspace() {
        guard let path = pickWorkspace(create: false) else { return }
        guard FileManager.default.fileExists(atPath: path + "/config/config.json") else {
            alert(t("不是有效工作区", "Not a valid workspace"),
                  t("所选目录缺少 config/config.json。\n若是全新开始，请改用「首次设置…」。",
                    "The chosen directory has no config/config.json.\nFor a fresh start, use “First-time setup…”.")); return
        }
        runSetup(["setup", "--workspace", path]) { self.finishSetup($0, onboard: false) }
    }
    func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
    func openOnboard(_ workspace: String, _ python: String) {
        let script = "cd \(shellQuote(workspace)) && \(shellQuote(python)) -m wq onboard; echo; read -n1\n"
        let path = NSTemporaryDirectory() + "wq-onboard-" + UUID().uuidString + ".command"
        do {
            try script.write(toFile: path, atomically: true, encoding: .utf8)
            chmod(path, 0o755)
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
            process.arguments = ["-a", "Terminal", path]
            try process.run()
        } catch { alert(t("无法打开 Terminal", "Cannot open Terminal"), error.localizedDescription) }
    }
    @objc func activate() {
        activateRow.isEnabled = false
        runSetup(["activate", "--workspace", root, "--app", Bundle.main.bundlePath]) { result in
            self.activateRow.isEnabled = true
            if let error = result["error"] as? String {
                self.alert(self.t("启用失败", "Enable failed"), error)
            } else {
                self.alert(self.t("已启用", "Enabled"),
                           self.t("调度器与登录自启已安装；此后菜单栏将随登录自动启动。",
                                  "Scheduler and login auto-start installed; the menu bar now launches at login."))
                self.fetched.removeAll(); self.refresh()
            }
        }
    }
    func updateActivateRow() {
        for row in [activateRow, activateSep] where settingsMenu.index(of: row) >= 0 {
            settingsMenu.removeItem(row)
        }
        if FileManager.default.fileExists(atPath: root + "/config/config.json") {
            activateRow.title = t("启用调度与自启", "Enable scheduling & auto-start")
            activateRow.target = self
            activateRow.toolTip = t("安装每 60 秒调度与登录自启（LaunchAgents）",
                                    "Install 60-second scheduling and login auto-start (LaunchAgents)")
            settingsMenu.insertItem(activateRow, at: 0)
            settingsMenu.insertItem(activateSep, at: 1)
        }
    }
    func enterMainMode() {
        ready = true
        lockFD = open(root + "/var/run/menubar.lock", O_CREAT | O_RDWR, 0o600)
        guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else { NSApp.terminate(nil); return }
        buildMainMenu()
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        refresh()
        perform("history"); perform("submissions"); perform("notifications"); perform("settings")
        if timer == nil {
            timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in self?.refresh() }
        }
        if notifyTimer == nil {
            notifyTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.perform("notifications") }
        }
    }
    func buildMainMenu() {
        // 语言切换后整棵静态菜单按当前 lang 重建；动态数据由后续 fill* 回填。
        controls.removeAll()
        menu.removeAllItems()
        for row in [headline, detail, tick, nextAt, researchModel, reviewModel] { row.isEnabled = false; menu.addItem(row) }
        menu.addItem(.separator())
        accountMenu = NSMenu(title: t("WorldQuant 账号", "WorldQuant account"))
        accountMenu.autoenablesItems = false
        textRow(brainRow, t("BRAIN 账号：读取中…", "BRAIN account: loading…"))
        accountMenu.addItem(brainRow)
        accountMenu.addItem(.separator())
        for (title, selector, tip) in [
            (t("绑定 / 重新登录…", "Bind / re-login…"), #selector(brainLogin),
             t("在 Terminal 中打开 BRAIN 登录：按提示输入邮箱与密码，密码不保存。",
               "Opens BRAIN login in Terminal: enter your email and password when prompted; the password is not saved.")),
            (t("核验会话（联网检查）", "Verify session (online check)"), #selector(brainCheck),
             t("用已保存的会话访问 BRAIN 只读预检；不发起模拟或提交。",
               "Accesses a BRAIN read-only preflight with the saved session; no simulations or submissions.")),
            (t("打开 BRAIN 注册页", "Open BRAIN registration page"), #selector(brainRegister),
             t("在浏览器打开官方注册入口。", "Opens the official registration page in the browser."))
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; accountMenu.addItem(row); controls.append(row)
        }
        let accountRow = NSMenuItem(title: t("WorldQuant 账号", "WorldQuant account"), action: nil, keyEquivalent: "")
        accountRow.submenu = accountMenu; accountRow.isEnabled = true
        menu.addItem(accountRow)
        menu.addItem(.separator())
        historyMenu = NSMenu(title: t("轮次历史", "Cycle history"))
        submissionsMenu = NSMenu(title: t("已提交 Alpha", "Submitted Alphas"))
        for (row, submenu) in [(cycles, historyMenu), (submissions, submissionsMenu)] {
            submenu.autoenablesItems = false; submenu.delegate = self
            submenu.addItem(info(t("正在读取本地账本…", "Reading the local ledger…")))
            row.submenu = submenu; row.isEnabled = true; menu.addItem(row)
        }
        menu.addItem(.separator())
        settingsMenu = NSMenu(title: t("设置", "Settings"))
        presetMenu = NSMenu(title: t("路由预设", "Routing presets"))
        providerMenu = NSMenu(title: t("渠道", "Providers"))
        intervalMenu = NSMenu(title: t("运行间隔", "Run interval"))
        dailyMenu = NSMenu(title: t("每日轮数上限", "Daily cycle limit"))
        totalMenu = NSMenu(title: t("累计轮数上限", "Total cycle limit"))
        languageMenu = NSMenu(title: t("界面语言", "Interface language"))
        settingsMenu.autoenablesItems = false; settingsMenu.delegate = self
        settingsMenu.addItem(notifyRow)
        settingsMenu.addItem(.separator())
        for (title, submenu, tip) in [
            (t("界面语言", "Interface language"), languageMenu,
             t("自动跟随系统：中文环境用中文，其余用英文；也可固定中文或英文。",
               "Automatic follows the system locale (Chinese for zh*, English otherwise); or fix Chinese / English.")),
            (t("路由预设", "Routing presets"), presetMenu,
             t("切换整套餐路：研究与审查的渠道顺序；下一项任务领取时生效，在途任务保持原路由。",
               "Switch the whole route set: provider order for research and review; applies to the next claimed task. In-flight tasks keep their routes.")),
            (t("渠道", "Providers"), providerMenu,
             t("临时停用或恢复单个渠道；不打断在途调用，预算闸门保留。",
               "Temporarily disable or re-enable one provider; in-flight calls are unaffected, budget gates remain.")),
            (t("运行间隔", "Run interval"), intervalMenu,
             t("两轮研究之间的等待时长；下一次调度起采用。",
               "Wait between research cycles; applies from the next scheduling tick.")),
            (t("每日轮数上限", "Daily cycle limit"), dailyMenu,
             t("每个 UTC 日最多启动的研究轮数。", "Research cycles started at most per UTC day.")),
            (t("累计轮数上限", "Total cycle limit"), totalMenu,
             t("累计研究轮数达到上限后停止启动新轮次。", "No new cycles once the total reaches this limit."))
        ] {
            submenu.autoenablesItems = false
            submenu.addItem(info(t("正在读取…", "Loading…")))
            let row = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            row.submenu = submenu; row.toolTip = tip; row.isEnabled = true
            settingsMenu.addItem(row)
        }
        let settingsRow = NSMenuItem(title: t("设置", "Settings"), action: nil, keyEquivalent: "")
        settingsRow.submenu = settingsMenu; settingsRow.isEnabled = true
        menu.addItem(settingsRow)
        menu.addItem(.separator())
        for (title, selector, tip) in [
            (t("立刻运行下一轮", "Run next cycle now"), #selector(runNext),
             t("跳过轮间等待，立即请求一轮；额度、授权与平台冷却仍然生效，不改变自动运行开关。",
               "Skips the wait and requests one cycle now; quota, authorization and platform cooldown still apply. The auto-run switch is unchanged.")),
            (t("开始自动运行", "Start automatic research"), #selector(start),
             t("恢复队列，按既有间隔持续研究。", "Resumes the queue and keeps researching at the configured interval.")),
            (t("暂停（当前任务完成后）", "Pause (after current task finishes)"), #selector(pause),
             t("停止领取新任务；已领取任务允许收尾。", "Stops claiming new tasks; claimed tasks may finish.")),
            (t("打开运行日志", "Open run log"), #selector(logs),
             t("查看本机调度日志。", "View the local scheduler log.")),
            (t("退出（暂停自动运行）", "Quit (pauses automatic research)"), #selector(quit),
             t("先暂停队列，再关闭菜单栏图标。", "Pauses the queue first, then closes the menu bar icon."))
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; menu.addItem(row); controls.append(row)
        }
        notifyRow.title = t("系统通知", "System notifications")
        notifyRow.target = self
        notifyRow.toolTip = t("Alpha 提交成功或任务失败时发送 macOS 系统通知；需在系统设置中允许本应用通知。",
                              "Sends macOS notifications when an Alpha is accepted or a task fails; allow notifications for this app in System Settings.")
        notifyRow.representedObject = "config=notifications=off"   // 默认开启，点击即关闭；fillSettings 会按实际状态刷新
        updateActivateRow()
    }
    func menuWillOpen(_ opened: NSMenu) {
        guard ready else { return }
        if opened === historyMenu { load("history") }
        else if opened === submissionsMenu { load("submissions") }
        else if opened === settingsMenu { updateActivateRow(); load("settings") }
        else if opened === menu { refresh() }
    }
    func load(_ kind: String) {
        if fetched[kind] == nil || Date().timeIntervalSince(fetched[kind]!) > 30 { perform(kind) }
    }
    func refresh() { if !actionBusy { perform("status") } }
    @objc func runNext() { perform("run-next") }
    @objc func start() { perform("start") }
    @objc func pause() { perform("pause") }
    @objc func quit() { perform("quit") }
    @objc func brainLogin() { perform("brain-login") }
    @objc func brainCheck() { perform("brain-check") }
    @objc func brainRegister() { perform("brain-register") }
    @objc func logs() { NSWorkspace.shared.open(URL(fileURLWithPath: root + "/var/run/launchd.out.log")) }
    @objc func apply(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        let parts = code.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        if parts[0] == "preset", parts.count > 1 {
            // 切换预设前让用户选择：仅下一个新建轮次使用，还是永久切换。
            let box = NSAlert()
            box.messageText = t("切换路由预设：", "Switch routing preset: ") + parts[1]
            box.informativeText = t("仅切换一轮：下一个新建的研究轮次使用该预设，结束后自动恢复当前永久预设。\n永久切换：之后所有新任务都使用该预设。\n在途任务都保持原路由。",
                                    "One cycle only: the next new research cycle uses this preset, then the current permanent preset resumes.\nPermanent: all new tasks use it from now on.\nIn-flight tasks keep their routes either way.")
            box.addButton(withTitle: t("仅切换一轮", "One cycle only"))
            box.addButton(withTitle: t("永久切换", "Permanent"))
            box.addButton(withTitle: t("取消", "Cancel"))
            NSApp.activate(ignoringOtherApps: true)
            switch box.runModal() {
            case .alertFirstButtonReturn: perform("preset-once", parts[1])
            case .alertSecondButtonReturn: perform("preset", parts[1])
            default: return
            }
            return
        }
        perform(parts[0], parts.count > 1 ? parts[1] : nil)
    }
    func pickRow(_ title: String, _ code: String, on: Bool, tip: String, in target: NSMenu) {
        let row = NSMenuItem(title: title, action: #selector(apply(_:)), keyEquivalent: "")
        row.target = self; row.representedObject = code
        row.state = on ? .on : .off; row.toolTip = tip
        target.addItem(row)
    }

    func fill(_ kind: String, _ response: [String: Any]) {
        let target = kind == "history" ? historyMenu : submissionsMenu
        target.removeAllItems()
        let entries = response["entries"] as? [[String: Any]] ?? []
        if entries.isEmpty { target.addItem(info(t("暂无记录", "No records"))) }
        if kind == "history" {
            target.addItem(info(t("共 \(entries.count) 轮 · 北京时间", "\(entries.count) cycles · Beijing time")))
            target.addItem(.separator())
            for entry in entries {
                let lines = entry["lines"] as? [String] ?? []
                let title = entry["title"] as? String ?? ""
                let badge = entry["badge"] as? String ?? ""
                let color: NSColor? = badge == "submitted" ? .systemGreen
                    : badge == "failed" ? .systemRed
                    : badge == "progress" ? .systemBlue : nil
                let row = block([(badge == "submitted" ? "★ " : "") + title] + lines, titleColor: color)
                row.toolTip = (entry["detail"] as? [String] ?? lines).joined(separator: "\n")
                row.view?.toolTip = row.toolTip
                target.addItem(row)
                target.addItem(.separator())
            }
        } else {
            submissions.title = t("已提交 Alpha（\(entries.count)）", "Submitted Alphas (\(entries.count))")
            for entry in entries {
                let badge = entry["badge"] as? String ?? ""
                let title = (badge == "submitted" ? "★ " : "") + (entry["title"] as? String ?? "")
                let row = NSMenuItem(title: title, action: nil, keyEquivalent: "")
                if badge == "submitted" {
                    row.attributedTitle = NSAttributedString(string: title, attributes: [
                        .foregroundColor: NSColor.systemGreen,
                        .font: NSFont.systemFont(ofSize: 13, weight: .semibold)])
                }
                let submenu = NSMenu(); submenu.autoenablesItems = false
                let lines = entry["lines"] as? [String] ?? []
                for line in lines { submenu.addItem(block([line])) }
                row.submenu = submenu; row.isEnabled = true; target.addItem(row)
            }
        }
        fetched[kind] = Date()
    }

    func fillSettings(_ response: [String: Any]) {
        let responseLang = response["language"] as? String ?? "zh"
        if responseLang != lang { lang = responseLang; buildMainMenu() }   // 先按新语言重建，再回填动态数据
        let notifications = response["notifications"] as? Bool ?? true
        notifyRow.state = notifications ? .on : .off
        notifyRow.representedObject = "config=notifications=" + (notifications ? "off" : "on")
        let setting = response["language_setting"] as? String ?? "auto"
        languageMenu.removeAllItems()
        for (value, title) in [("auto", t("跟随系统（自动）", "Follow system (auto)")),
                               ("zh", "中文"), ("en", "English")] {
            pickRow(title, "config=language=\(value)", on: setting == value,
                    tip: t("写入 config.json 的 ui.language；auto 表示跟随系统。",
                           "Writes ui.language in config.json; auto follows the system."), in: languageMenu)
        }
        presetMenu.removeAllItems()
        let active = response["active_preset"] as? String ?? ""
        let permanent = response["permanent_preset"] as? String ?? active
        let once = response["preset_once"] as? String ?? ""
        let cyclePreset = response["cycle_preset"] as? String ?? ""
        var head = t("永久预设：", "Permanent preset: ") + permanent
        if !cyclePreset.isEmpty { head += t("｜本轮临时：", " | this cycle: ") + cyclePreset }
        if !once.isEmpty { head += t("｜下一轮临时：", " | next cycle: ") + once }
        presetMenu.addItem(info(head))
        presetMenu.addItem(info(t("点击预设后选择：仅切换一轮 / 永久切换", "Click a preset to choose: one cycle only / permanent")))
        presetMenu.addItem(NSMenuItem.separator())
        for preset in response["presets"] as? [[String: Any]] ?? [] {
            let name = preset["name"] as? String ?? ""
            pickRow(name, "preset=" + name, on: name == active,
                    tip: (preset["routes"] as? String ?? ""), in: presetMenu)
        }
        providerMenu.removeAllItems()
        providerMenu.addItem(info(t("勾选表示参与路由，点击切换", "Checked = used for routing; click to toggle")))
        for provider in response["providers"] as? [[String: Any]] ?? [] {
            let name = provider["name"] as? String ?? ""
            let disabled = provider["disabled"] as? Bool ?? false
            let reason = provider["reason"] as? String ?? ""
            pickRow(name, "provider=" + name, on: !disabled,
                    tip: (provider["label"] as? String ?? "") + (reason.isEmpty ? t("；当前可用", "; available") : "；" + reason),
                    in: providerMenu)
        }
        let intervals: [(Int, String, String)] = [(300, "5 分钟", "5 min"), (900, "15 分钟", "15 min"), (1800, "30 分钟", "30 min"),
                                                   (3600, "1 小时", "1 hour"), (7200, "2 小时", "2 hours"), (21600, "6 小时", "6 hours")]
        let currentInterval = response["interval_s"] as? Int ?? 0
        intervalMenu.removeAllItems()
        for (seconds, zh, en) in intervals {
            pickRow(t(zh, en), "config=interval_s=\(seconds)", on: seconds == currentInterval,
                    tip: t("两轮研究之间的等待时长", "Wait between research cycles"), in: intervalMenu)
        }
        if currentInterval > 0 && !intervals.contains(where: { $0.0 == currentInterval }) {
            intervalMenu.addItem(info(t("当前值 \(currentInterval) 秒", "Current: \(currentInterval) s")))
        }
        let dailies: [(Int, String, String)] = [(10, "10 轮", "10 cycles"), (20, "20 轮", "20 cycles"),
                                                 (40, "40 轮", "40 cycles"), (80, "80 轮", "80 cycles")]
        let currentDaily = response["max_cycles_per_day"] as? Int ?? 0
        dailyMenu.removeAllItems()
        for (value, zh, en) in dailies {
            pickRow(t(zh, en), "config=max_cycles_per_day=\(value)", on: value == currentDaily,
                    tip: t("每个 UTC 日最多启动的研究轮数", "Research cycles started at most per UTC day"), in: dailyMenu)
        }
        if currentDaily > 0 && !dailies.contains(where: { $0.0 == currentDaily }) {
            dailyMenu.addItem(info(t("当前值 \(currentDaily) 轮", "Current: \(currentDaily) cycles")))
        }
        let totals: [(Int, String, String)] = [(50, "50 轮", "50 cycles"), (100, "100 轮", "100 cycles"),
                                                (150, "150 轮", "150 cycles"), (300, "300 轮", "300 cycles")]
        let currentTotal = response["max_cycles_total"] as? Int
        totalMenu.removeAllItems()
        for (value, zh, en) in totals {
            pickRow(t(zh, en), "config=max_cycles_total=\(value)", on: value == currentTotal,
                    tip: t("累计研究轮数达到上限后停止启动新轮次", "No new cycles once the total reaches this limit"), in: totalMenu)
        }
        pickRow(t("不限", "Unlimited"), "config=max_cycles_total=none", on: currentTotal == nil,
                tip: t("不设累计上限，由预算与授权窗口约束", "No total cap; bounded by budget and the authorization window"), in: totalMenu)
        if let current = currentTotal, !totals.contains(where: { $0.0 == current }) {
            totalMenu.addItem(info(t("当前值 \(current) 轮", "Current: \(current) cycles")))
        }
        fetched["settings"] = Date()
    }

    func perform(_ action: String, _ arg: String? = nil) {
        let readOnly = ["status", "history", "submissions", "settings", "notifications"].contains(action)
        if loading.contains(action) || (!readOnly && actionBusy) { return }
        loading.insert(action)
        if !readOnly {
            actionBusy = true; controls.forEach { $0.isEnabled = false }; textRow(headline, t("正在处理…", "Working…"))
        }
        DispatchQueue.global(qos: .utility).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.python)
            process.arguments = ["-m", "wq.desktop_control", action] + (arg.map { [$0] } ?? [])
            process.currentDirectoryURL = URL(fileURLWithPath: self.root)
            var environment = ProcessInfo.processInfo.environment
            if FileManager.default.fileExists(atPath: self.root + "/src/wq/__init__.py") {
                let src = self.root + "/src"
                environment["PYTHONPATH"] = environment["PYTHONPATH"].map { src + ":" + $0 } ?? src
            }
            process.environment = environment
            let pipe = Pipe(); process.standardOutput = pipe; process.standardError = pipe
            var result: [String: Any] = [:]; var success = false
            do {
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                result = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
                success = process.terminationStatus == 0 && result["error"] == nil
                if !success && result["error"] == nil { result["error"] = self.t("控制程序失败，请查看本地日志", "Control program failed; see the local log") }
            } catch { result["error"] = error.localizedDescription }
            let response = result; let ok = success
            DispatchQueue.main.async {
                self.loading.remove(action)
                if !readOnly { self.actionBusy = false; self.controls.forEach { $0.isEnabled = true } }
                if !ok {
                    let error = response["error"] as? String ?? self.t("读取失败", "Read failed")
                    if action == "history" || action == "submissions" {
                        let target = action == "history" ? self.historyMenu : self.submissionsMenu
                        target.removeAllItems(); target.addItem(self.block([error])); return
                    }
                    self.fetched["settings"] = nil
                    self.textRow(self.headline, self.t("读取失败", "Read failed"))
                    self.textRow(self.detail, String(error.prefix(90)))
                    self.item.button?.toolTip = error
                    if !readOnly {
                        let alert = NSAlert(); alert.messageText = self.t("操作未完成", "Action failed"); alert.informativeText = error; alert.runModal()
                    }
                } else if action == "quit" { NSApp.terminate(nil)
                } else if action == "history" || action == "submissions" { self.fill(action, response)
                } else if action == "settings" { self.fillSettings(response)
                } else if action == "notifications" { self.postNotifications(response)
                } else if action == "status" {
                    if self.actionBusy { return }
                    self.textRow(self.headline, response["title"] as? String ?? self.t("状态未知", "Status unknown"))
                    self.textRow(self.detail, String((response["message"] as? String ?? "").prefix(90)))
                    self.cycles.title = self.t("轮次历史", "Cycle history")
                    self.cycles.toolTip = response["cycles"] as? String ?? ""
                    self.textRow(self.tick, self.t("最近调度：", "Last tick: ") + (response["last_tick"] as? String ?? self.t("尚无记录", "No records yet")))
                    self.textRow(self.nextAt, self.t("下一轮：", "Next cycle: ") + (response["next_at"] as? String ?? self.t("待当前任务完成／调度检查", "awaiting current task / scheduler check")))
                    self.textRow(self.brainRow, self.t("BRAIN 账号：", "BRAIN account: ") + ((response["brain_bound"] as? Bool) == true ? self.t("已绑定（本地会话存在）", "bound (local session present)") : self.t("未绑定；用「绑定 / 重新登录」", "not bound; use “Bind / re-login”")))
                    let models = response["next_models"] as? [[String: Any]] ?? []
                    for (index, row) in [self.researchModel, self.reviewModel].enumerated() {
                        self.textRow(row, self.t("下轮", "Next: ") + (index < models.count ? models[index]["title"] as? String ?? self.t("未知", "unknown") : self.t("未知", "unknown")))
                        row.toolTip = self.t("预估下一轮路由；任务领取时冻结，重试可能切换备用渠道。",
                                             "Estimated routing for the next cycle; frozen at claim time, retries may switch to a fallback provider.")
                    }
                    self.item.button?.alphaValue = response["paused"] as? Bool == true ? 0.5 : 1
                    self.item.button?.toolTip = self.headline.title + "\n" + self.detail.title
                } else if action.hasPrefix("brain-") {
                    if let message = response["message"] as? String, !message.isEmpty {
                        self.alert("WorldQuant", message)
                    }
                    self.refresh()
                } else {
                    self.fetched.removeAll()
                    self.load("settings")
                    self.refresh()
                }
            }
        }
    }
    func postNotifications(_ response: [String: Any]) {
        let items = response["items"] as? [[String: Any]] ?? []
        guard !items.isEmpty else { return }
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized else { return }
            for item in items {
                let content = UNMutableNotificationContent()
                content.title = item["title"] as? String ?? "WorldQuant"
                content.body = item["body"] as? String ?? ""
                content.sound = .default
                let request = UNNotificationRequest(identifier: item["id"] as? String ?? UUID().uuidString,
                                                    content: content, trigger: nil)
                center.add(request)
            }
        }
    }
}
extension AppDelegate: UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
