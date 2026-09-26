import AppKit
import Darwin
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var item: NSStatusItem!
    let menu = NSMenu()
    let historyMenu = NSMenu(title: "轮次历史")
    let submissionsMenu = NSMenu(title: "已提交 Alpha")
    let settingsMenu = NSMenu(title: "设置")
    let accountMenu = NSMenu(title: "WorldQuant 账号")
    let presetMenu = NSMenu(title: "路由预设")
    let providerMenu = NSMenu(title: "渠道")
    let intervalMenu = NSMenu(title: "运行间隔")
    let dailyMenu = NSMenu(title: "每日轮数上限")
    let totalMenu = NSMenu(title: "累计轮数上限")
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
    let activateRow = NSMenuItem(title: "启用调度与自启", action: #selector(activate), keyEquivalent: "")
    let activateSep = NSMenuItem.separator()
    let headline = NSMenuItem(title: "读取状态…", action: nil, keyEquivalent: "")
    let detail = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let cycles = NSMenuItem(title: "轮次历史", action: nil, keyEquivalent: "")
    let submissions = NSMenuItem(title: "已提交 Alpha", action: nil, keyEquivalent: "")
    let tick = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let nextAt = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let researchModel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let reviewModel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let brainRow = NSMenuItem(title: "BRAIN 账号：读取中…", action: nil, keyEquivalent: "")
    let notifyRow = NSMenuItem(title: "系统通知", action: #selector(apply(_:)), keyEquivalent: "")
    var controls: [NSMenuItem] = []

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
            alert("请先安装到「应用程序」", "WorldQuant 不能从磁盘映像或临时位置运行。\n请把 WorldQuant.app 拖入「应用程序」文件夹，再从那里启动。")
            NSApp.terminate(nil); return
        }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let path = Bundle.main.path(forResource: "ResearchIcon", ofType: "png"), let icon = NSImage(contentsOfFile: path) {
            icon.size = NSSize(width: 22, height: 22); icon.isTemplate = true
            item.button?.image = icon
        } else { item.button?.title = "WQ" }
        item.button?.setAccessibilityLabel("WorldQuant 自动研究")
        menu.autoenablesItems = false
        menu.delegate = self
        item.menu = menu
        if configured { enterMainMode() } else { buildFirstRunMenu() }
    }
    func buildFirstRunMenu() {
        menu.removeAllItems()
        menu.addItem(info("尚未配置工作区"))
        menu.addItem(.separator())
        for (title, selector, tip) in [
            ("首次设置…", #selector(firstSetup), "选择或新建工作区目录，安装内置引擎运行时，并在终端完成初始向导。"),
            ("选择已有工作区…", #selector(chooseWorkspace), "关联一个已包含 config/config.json 的工作区目录。")
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; menu.addItem(row)
        }
        menu.addItem(.separator())
        let quitRow = NSMenuItem(title: "退出", action: #selector(quitFirstRun), keyEquivalent: "")
        quitRow.target = self; menu.addItem(quitRow)
    }
    @objc func quitFirstRun() { NSApp.terminate(nil) }
    func pickWorkspace(create: Bool) -> String? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false; panel.canChooseDirectories = true
        panel.canCreateDirectories = true; panel.allowsMultipleSelection = false
        panel.prompt = "选择"
        let fallback = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("autowq")
        panel.directoryURL = FileManager.default.fileExists(atPath: fallback.path) ? fallback
            : FileManager.default.homeDirectoryForCurrentUser
        panel.message = create ? "选择或新建一个目录作为工作区（建议 ~/autowq）" : "选择一个已包含 config/config.json 的工作区目录"
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return url.path
    }
    func runSetup(_ arguments: [String], _ completion: @escaping ([String: Any]) -> Void) {
        guard let script = Bundle.main.path(forResource: "app_setup", ofType: "py") else {
            completion(["error": "应用包缺少 app_setup.py"]); return
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
                    result["error"] = "安装脚本失败（退出码 \(process.terminationStatus)）"
                }
            } catch { result = ["error": error.localizedDescription] }
            DispatchQueue.main.async {
                self.item.button?.appearsDisabled = false
                completion(result)
            }
        }
    }
    func finishSetup(_ result: [String: Any], onboard: Bool) {
        if let error = result["error"] as? String { alert("设置未完成", error); return }
        guard let workspace = result["workspace"] as? String, let python = result["python"] as? String else {
            alert("设置未完成", "安装脚本返回了无法识别的结果。"); return
        }
        UserDefaults.standard.set(workspace, forKey: "WQWorkspace")
        UserDefaults.standard.set(python, forKey: "WQPython")
        if onboard { openOnboard(workspace, python) }
        alert("运行时已就绪", onboard
            ? "已在 Terminal 中打开 onboard 向导。\n完成后回到本菜单，在「设置」中选择「启用调度与自启」。"
            : "工作区已关联。如需开机自启与定时调度，在「设置」中选择「启用调度与自启」。")
        enterMainMode()
    }
    @objc func firstSetup() {
        guard let path = pickWorkspace(create: true) else { return }
        runSetup(["setup", "--workspace", path]) { self.finishSetup($0, onboard: true) }
    }
    @objc func chooseWorkspace() {
        guard let path = pickWorkspace(create: false) else { return }
        guard FileManager.default.fileExists(atPath: path + "/config/config.json") else {
            alert("不是有效工作区", "所选目录缺少 config/config.json。\n若是全新开始，请改用「首次设置…」。"); return
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
        } catch { alert("无法打开 Terminal", error.localizedDescription) }
    }
    @objc func activate() {
        activateRow.isEnabled = false
        runSetup(["activate", "--workspace", root, "--app", Bundle.main.bundlePath]) { result in
            self.activateRow.isEnabled = true
            if let error = result["error"] as? String {
                self.alert("启用失败", error)
            } else {
                self.alert("已启用", "调度器与登录自启已安装；此后菜单栏将随登录自动启动。")
                self.fetched.removeAll(); self.refresh()
            }
        }
    }
    func updateActivateRow() {
        for row in [activateRow, activateSep] where settingsMenu.index(of: row) >= 0 {
            settingsMenu.removeItem(row)
        }
        if FileManager.default.fileExists(atPath: root + "/config/config.json") {
            activateRow.target = self
            activateRow.toolTip = "安装每 60 秒调度与登录自启（LaunchAgents）"
            settingsMenu.insertItem(activateRow, at: 0)
            settingsMenu.insertItem(activateSep, at: 1)
        }
    }
    func enterMainMode() {
        ready = true
        lockFD = open(root + "/var/run/menubar.lock", O_CREAT | O_RDWR, 0o600)
        guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else { NSApp.terminate(nil); return }
        menu.removeAllItems()
        for row in [headline, detail, tick, nextAt, researchModel, reviewModel] { row.isEnabled = false; menu.addItem(row) }
        menu.addItem(.separator())
        accountMenu.autoenablesItems = false
        textRow(brainRow, "BRAIN 账号：读取中…")
        accountMenu.addItem(brainRow)
        accountMenu.addItem(.separator())
        for (title, selector, tip) in [
            ("绑定 / 重新登录…", #selector(brainLogin), "在 Terminal 中打开 BRAIN 登录：按提示输入邮箱与密码，密码不保存。"),
            ("核验会话（联网检查）", #selector(brainCheck), "用已保存的会话访问 BRAIN 只读预检；不发起模拟或提交。"),
            ("打开 BRAIN 注册页", #selector(brainRegister), "在浏览器打开官方注册入口。")
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; accountMenu.addItem(row); controls.append(row)
        }
        let accountRow = NSMenuItem(title: "WorldQuant 账号", action: nil, keyEquivalent: "")
        accountRow.submenu = accountMenu; accountRow.isEnabled = true
        menu.addItem(accountRow)
        menu.addItem(.separator())
        for (row, submenu) in [(cycles, historyMenu), (submissions, submissionsMenu)] {
            submenu.autoenablesItems = false; submenu.delegate = self
            submenu.addItem(info("正在读取本地账本…"))
            row.submenu = submenu; row.isEnabled = true; menu.addItem(row)
        }
        menu.addItem(.separator())
        settingsMenu.autoenablesItems = false; settingsMenu.delegate = self
        settingsMenu.addItem(notifyRow)
        settingsMenu.addItem(.separator())
        for (title, submenu, tip) in [
            ("路由预设", presetMenu, "切换整套餐路：研究与审查的渠道顺序；下一项任务领取时生效，在途任务保持原路由。"),
            ("渠道", providerMenu, "临时停用或恢复单个渠道；不打断在途调用，预算闸门保留。"),
            ("运行间隔", intervalMenu, "两轮研究之间的等待时长；下一次调度起采用。"),
            ("每日轮数上限", dailyMenu, "每个 UTC 日最多启动的研究轮数。"),
            ("累计轮数上限", totalMenu, "累计研究轮数达到上限后停止启动新轮次。")
        ] {
            submenu.autoenablesItems = false
            submenu.addItem(info("正在读取…"))
            let row = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            row.submenu = submenu; row.toolTip = tip; row.isEnabled = true
            settingsMenu.addItem(row)
        }
        let settingsRow = NSMenuItem(title: "设置", action: nil, keyEquivalent: "")
        settingsRow.submenu = settingsMenu; settingsRow.isEnabled = true
        menu.addItem(settingsRow)
        menu.addItem(.separator())
        for (title, selector, tip) in [
            ("立刻运行下一轮", #selector(runNext), "跳过轮间等待，立即请求一轮；额度、授权与平台冷却仍然生效，不改变自动运行开关。"),
            ("开始自动运行", #selector(start), "恢复队列，按既有间隔持续研究。"),
            ("暂停（当前任务完成后）", #selector(pause), "停止领取新任务；已领取任务允许收尾。"),
            ("打开运行日志", #selector(logs), "查看本机调度日志。"),
            ("退出（暂停自动运行）", #selector(quit), "先暂停队列，再关闭菜单栏图标。")
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; menu.addItem(row); controls.append(row)
        }
        notifyRow.target = self
        notifyRow.toolTip = "Alpha 提交成功或任务失败时发送 macOS 系统通知；需在系统设置中允许本应用通知。"
        notifyRow.representedObject = "config=notifications=off"   // 默认开启，点击即关闭；fillSettings 会按实际状态刷新
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        refresh()
        perform("history"); perform("submissions"); perform("notifications")
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in self?.refresh() }
        notifyTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.perform("notifications") }
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
            box.messageText = "切换路由预设：" + parts[1]
            box.informativeText = "仅切换一轮：下一个新建的研究轮次使用该预设，结束后自动恢复当前永久预设。\n永久切换：之后所有新任务都使用该预设。\n在途任务都保持原路由。"
            box.addButton(withTitle: "仅切换一轮")
            box.addButton(withTitle: "永久切换")
            box.addButton(withTitle: "取消")
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
        if entries.isEmpty { target.addItem(info("暂无记录")) }
        if kind == "history" {
            target.addItem(info("共 \(entries.count) 轮 · 北京时间"))
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
                target.addItem(row)
                target.addItem(.separator())
            }
        } else {
            submissions.title = "已提交 Alpha（\(entries.count)）"
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
        let notifications = response["notifications"] as? Bool ?? true
        notifyRow.state = notifications ? .on : .off
        notifyRow.representedObject = "config=notifications=" + (notifications ? "off" : "on")
        presetMenu.removeAllItems()
        let active = response["active_preset"] as? String ?? ""
        let permanent = response["permanent_preset"] as? String ?? active
        let once = response["preset_once"] as? String ?? ""
        let cyclePreset = response["cycle_preset"] as? String ?? ""
        var head = "永久预设：" + permanent
        if !cyclePreset.isEmpty { head += "｜本轮临时：" + cyclePreset }
        if !once.isEmpty { head += "｜下一轮临时：" + once }
        presetMenu.addItem(info(head))
        presetMenu.addItem(info("点击预设后选择：仅切换一轮 / 永久切换"))
        presetMenu.addItem(NSMenuItem.separator())
        for preset in response["presets"] as? [[String: Any]] ?? [] {
            let name = preset["name"] as? String ?? ""
            pickRow(name, "preset=" + name, on: name == active,
                    tip: (preset["routes"] as? String ?? ""), in: presetMenu)
        }
        providerMenu.removeAllItems()
        providerMenu.addItem(info("勾选表示参与路由，点击切换"))
        for provider in response["providers"] as? [[String: Any]] ?? [] {
            let name = provider["name"] as? String ?? ""
            let disabled = provider["disabled"] as? Bool ?? false
            let reason = provider["reason"] as? String ?? ""
            pickRow(name, "provider=" + name, on: !disabled,
                    tip: (provider["label"] as? String ?? "") + (reason.isEmpty ? "；当前可用" : "；" + reason),
                    in: providerMenu)
        }
        let intervals: [(Int, String)] = [(300, "5 分钟"), (900, "15 分钟"), (1800, "30 分钟"),
                                          (3600, "1 小时"), (7200, "2 小时"), (21600, "6 小时")]
        let currentInterval = response["interval_s"] as? Int ?? 0
        intervalMenu.removeAllItems()
        for (seconds, text) in intervals {
            pickRow(text, "config=interval_s=\(seconds)", on: seconds == currentInterval,
                    tip: "两轮研究之间的等待时长", in: intervalMenu)
        }
        if currentInterval > 0 && !intervals.contains(where: { $0.0 == currentInterval }) {
            intervalMenu.addItem(info("当前值 \(currentInterval) 秒"))
        }
        let dailies: [(Int, String)] = [(10, "10 轮"), (20, "20 轮"), (40, "40 轮"), (80, "80 轮")]
        let currentDaily = response["max_cycles_per_day"] as? Int ?? 0
        dailyMenu.removeAllItems()
        for (value, text) in dailies {
            pickRow(text, "config=max_cycles_per_day=\(value)", on: value == currentDaily,
                    tip: "每个 UTC 日最多启动的研究轮数", in: dailyMenu)
        }
        if currentDaily > 0 && !dailies.contains(where: { $0.0 == currentDaily }) {
            dailyMenu.addItem(info("当前值 \(currentDaily) 轮"))
        }
        let totals: [(Int, String)] = [(50, "50 轮"), (100, "100 轮"), (150, "150 轮"), (300, "300 轮")]
        let currentTotal = response["max_cycles_total"] as? Int
        totalMenu.removeAllItems()
        for (value, text) in totals {
            pickRow(text, "config=max_cycles_total=\(value)", on: value == currentTotal,
                    tip: "累计研究轮数达到上限后停止启动新轮次", in: totalMenu)
        }
        pickRow("不限", "config=max_cycles_total=none", on: currentTotal == nil,
                tip: "不设累计上限，由预算与授权窗口约束", in: totalMenu)
        if let current = currentTotal, !totals.contains(where: { $0.0 == current }) {
            totalMenu.addItem(info("当前值 \(current) 轮"))
        }
        fetched["settings"] = Date()
    }

    func perform(_ action: String, _ arg: String? = nil) {
        let readOnly = ["status", "history", "submissions", "settings", "notifications"].contains(action)
        if loading.contains(action) || (!readOnly && actionBusy) { return }
        loading.insert(action)
        if !readOnly {
            actionBusy = true; controls.forEach { $0.isEnabled = false }; textRow(headline, "正在处理…")
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
                if !success && result["error"] == nil { result["error"] = "控制程序失败，请查看本地日志" }
            } catch { result["error"] = error.localizedDescription }
            let response = result; let ok = success
            DispatchQueue.main.async {
                self.loading.remove(action)
                if !readOnly { self.actionBusy = false; self.controls.forEach { $0.isEnabled = true } }
                if !ok {
                    let error = response["error"] as? String ?? "读取失败"
                    if action == "history" || action == "submissions" {
                        let target = action == "history" ? self.historyMenu : self.submissionsMenu
                        target.removeAllItems(); target.addItem(self.block([error])); return
                    }
                    self.fetched["settings"] = nil
                    self.textRow(self.headline, "需要处理")
                    self.textRow(self.detail, String(error.prefix(90)))
                    self.item.button?.toolTip = error
                    if !readOnly {
                        let alert = NSAlert(); alert.messageText = "操作未完成"; alert.informativeText = error; alert.runModal()
                    }
                } else if action == "quit" { NSApp.terminate(nil)
                } else if action == "history" || action == "submissions" { self.fill(action, response)
                } else if action == "settings" { self.fillSettings(response)
                } else if action == "notifications" { self.postNotifications(response)
                } else if action == "status" {
                    if self.actionBusy { return }
                    self.textRow(self.headline, response["title"] as? String ?? "状态未知")
                    self.textRow(self.detail, String((response["message"] as? String ?? "").prefix(90)))
                    self.cycles.title = "轮次历史"
                    self.cycles.toolTip = response["cycles"] as? String ?? ""
                    self.textRow(self.tick, "最近调度：" + (response["last_tick"] as? String ?? "尚无记录"))
                    self.textRow(self.nextAt, "下一轮：" + (response["next_at"] as? String ?? "待调度检查"))
                    self.textRow(self.brainRow, "BRAIN 账号：" + ((response["brain_bound"] as? Bool) == true ? "已绑定（本地会话存在）" : "未绑定；用「绑定 / 重新登录」"))
                    let models = response["next_models"] as? [[String: Any]] ?? []
                    for (index, row) in [self.researchModel, self.reviewModel].enumerated() {
                        self.textRow(row, "下轮" + (index < models.count ? models[index]["title"] as? String ?? "未知" : "未知"))
                        row.toolTip = "预估下一轮路由；任务领取时冻结，重试可能切换备用渠道。"
                    }
                    self.item.button?.alphaValue = response["paused"] as? Bool == true ? 0.5 : 1
                    self.item.button?.toolTip = self.headline.title + "\n" + self.detail.title
                } else if action.hasPrefix("brain-") {
                    if let message = response["message"] as? String, !message.isEmpty {
                        self.alert("WorldQuant 账号", message)
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
