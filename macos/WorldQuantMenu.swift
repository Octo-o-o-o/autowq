import AppKit
import Darwin
import UserNotifications

/// 预设切换对话框里的临时轮数输入（标签 + 数字框 + 步进器）；确认按钮标题随轮数实时更新。
final class PresetCyclesInput: NSObject {
    static let maxCycles = 100
    let alert: NSAlert
    let lang: String
    let field = NSTextField(string: "1")
    let stepper = NSStepper()
    let row = NSStackView()

    init(alert: NSAlert, lang: String) {
        self.alert = alert
        self.lang = lang
        super.init()
        let formatter = NumberFormatter()
        formatter.minimum = 1 as NSNumber; formatter.maximum = Self.maxCycles as NSNumber; formatter.allowsFloats = false
        field.formatter = formatter
        field.alignment = .center
        field.font = .monospacedDigitSystemFont(ofSize: 13, weight: .regular)
        field.widthAnchor.constraint(equalToConstant: 48).isActive = true
        field.target = self; field.action = #selector(changed(_:))
        stepper.minValue = 1; stepper.maxValue = Double(Self.maxCycles); stepper.valueWraps = false
        stepper.target = self; stepper.action = #selector(changed(_:))
        row.orientation = .horizontal
        row.spacing = 6
        row.addArrangedSubview(NSTextField(labelWithString: lang == "zh" ? "临时轮数" : "Cycles"))
        row.addArrangedSubview(field)
        row.addArrangedSubview(stepper)
    }
    func value() -> Int { max(1, min(Self.maxCycles, field.integerValue)) }
    func buttonTitle() -> String {
        let n = value()
        return lang == "zh" ? (n <= 1 ? "仅切换一轮" : "临时切换 \(n) 轮")
                            : (n <= 1 ? "One cycle only" : "Temporary for \(n) cycles")
    }
    @objc private func changed(_ sender: Any?) {
        if sender as? NSStepper === stepper { field.integerValue = stepper.integerValue }
        stepper.integerValue = value()   // 数字框手输越界时按钳制值同步回显
        field.integerValue = value()
        alert.buttons.first?.title = buttonTitle()
    }
}

final class CustomProviderForm: NSObject {
    let view = NSStackView()
    let nameField = NSTextField(string: "")
    let protocolPopup = NSPopUpButton()
    let urlField = NSTextField(string: "http://127.0.0.1:11434/v1")
    let modelField = NSTextField(string: "")
    let keyField = NSSecureTextField(string: "")
    let noKey = NSButton(checkboxWithTitle: "", target: nil, action: nil)

    init(lang: String) {
        super.init()
        let zh = lang == "zh"
        view.orientation = .vertical
        view.alignment = .leading
        view.spacing = 6
        view.setFrameSize(NSSize(width: 380, height: 196))
        protocolPopup.addItems(withTitles: ["openai", "anthropic"])
        noKey.title = zh ? "这个服务不需要 API Key" : "This service does not need an API key"
        func row(_ label: String, _ field: NSView) -> NSStackView {
            let line = NSStackView()
            line.orientation = .horizontal
            line.spacing = 8
            let title = NSTextField(labelWithString: label)
            title.widthAnchor.constraint(equalToConstant: 72).isActive = true
            if let text = field as? NSTextField { text.widthAnchor.constraint(equalToConstant: 260).isActive = true }
            line.addArrangedSubview(title)
            line.addArrangedSubview(field)
            return line
        }
        view.addArrangedSubview(row(zh ? "名称" : "Name", nameField))
        view.addArrangedSubview(row(zh ? "协议" : "Protocol", protocolPopup))
        view.addArrangedSubview(row(zh ? "地址" : "Address", urlField))
        view.addArrangedSubview(row(zh ? "模型 ID" : "Model ID", modelField))
        view.addArrangedSubview(row("API Key", keyField))
        view.addArrangedSubview(noKey)
    }
    func key() -> String? { noKey.state == .on ? nil : keyField.stringValue }
    func spec() -> String? {
        let name = nameField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let model = modelField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let url = urlField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if name.isEmpty || model.isEmpty || url.isEmpty { return nil }
        let payload: [String: Any] = [
            "name": name, "protocol": protocolPopup.titleOfSelectedItem ?? "openai",
            "base_url": url, "model": model, "allow_no_key": noKey.state == .on]
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else { return nil }
        return text
    }
}

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
    var modelMenu = NSMenu()
    var intervalMenu = NSMenu()
    var dailyMenu = NSMenu()
    var totalMenu = NSMenu()
    var languageMenu = NSMenu()
    var spendMenu = NSMenu()
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
    let standby = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    var standbyMenu = NSMenu()
    let tick = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let nextAt = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let researchModel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let reviewModel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let accountRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let brainRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let notifyRow = NSMenuItem(title: "", action: #selector(apply(_:)), keyEquivalent: "")
    let autoSubmitRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let submissionRow = NSMenuItem(title: "", action: #selector(apply(_:)), keyEquivalent: "")
    let launchRow = NSMenuItem(title: "", action: #selector(apply(_:)), keyEquivalent: "")
    let cycleRow = NSMenuItem(title: "", action: #selector(cycleAction), keyEquivalent: "")
    let powerRow = NSMenuItem(title: "", action: #selector(powerAction), keyEquivalent: "")
    var languageItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    var cycleCancels = false
    var powerStops = false
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
            applyIcon(active: false)
        } else { item.button?.title = "WQ" }
        item.button?.setAccessibilityLabel("WorldQuant")
        menu.autoenablesItems = false
        menu.delegate = self
        item.menu = menu
        if configured {
            menu.addItem(info(t("正在检查内置引擎更新…", "Checking bundled engine updates…")))
            runSetup(["setup", "--workspace", root, "--allow-busy"]) { self.finishEngineSetup($0) }
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
    func finishEngineSetup(_ result: [String: Any]) {
        if result["code"] as? String == "engine-busy" {
            skipLaunchBecauseBusy = true
            if let python = UserDefaults.standard.string(forKey: "WQPython"), !python.isEmpty {
                enterMainMode()
                return
            }
        }
        if let error = result["error"] as? String {
            alert(t("引擎更新未完成", "Engine update incomplete"), error)
            NSApp.terminate(nil)
            return
        }
        skipLaunchBecauseBusy = result["engine_pending"] as? Bool == true
        if let python = result["python"] as? String {
            UserDefaults.standard.set(python, forKey: "WQPython")
        }
        enterMainMode()
    }
    func offerEngineChoice() {
        let box = NSAlert()
        box.messageText = t("研究还在运行，引擎稍后再换", "Research is still running, so the engine waits")
        box.informativeText = t("更换内置引擎需要调度先放开锁。可以先打开菜单；研究继续，下次空闲时再更新。也可以退出，并选择本轮结束后停止，或立刻结束本地任务。已经发到平台的模拟不会撤回。",
                                "Replacing the bundled engine needs the scheduler to release its lock. Open the menu now and the engine updates the next time research is idle. Or quit and stop after this cycle, or stop local tasks now. A simulation already sent to the platform is not withdrawn.")
        box.addButton(withTitle: t("先打开菜单", "Open the menu"))
        box.addButton(withTitle: t("本轮结束后停止并退出", "Stop after this cycle and quit"))
        box.addButton(withTitle: t("立刻结束所有任务并更新", "Stop all tasks and update"))
        box.addButton(withTitle: t("仅退出", "Quit menu only"))
        NSApp.activate(ignoringOtherApps: true)
        switch box.runModal() {
        case .alertFirstButtonReturn:
            runSetup(["setup", "--workspace", root, "--allow-busy"]) { self.finishEngineSetup($0) }
        case .alertSecondButtonReturn:
            runSetup(["release", "--workspace", root, "--mode", "after-cycle"]) { result in
                if let error = result["error"] as? String {
                    self.alert(self.t("还不能按这个方式退出", "Could not quit that way"), error)
                    self.offerEngineChoice()
                    return
                }
                NSApp.terminate(nil)
            }
        case .alertThirdButtonReturn:
            runSetup(["release", "--workspace", root, "--mode", "now"]) { result in
                if result["code"] as? String == "engine-busy" || result["error"] != nil {
                    let error = result["error"] as? String ?? self.t("调度锁还没放开", "The scheduler still holds its lock")
                    self.alert(self.t("还不能更新引擎", "Engine update still blocked"), error)
                    self.offerEngineChoice()
                    return
                }
                self.runSetup(["setup", "--workspace", self.root]) { self.finishEngineSetup($0) }
            }
        default:
            NSApp.terminate(nil)
        }
    }
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
    /// 应用自带的 Python（与本进程同架构）；只有源码/旧版构建缺少内置运行时才退回系统 python3。
    func setupPython() -> String {
        #if arch(arm64)
        let arch = "arm64"
        #else
        let arch = "x86_64"
        #endif
        if let resources = Bundle.main.resourcePath {
            let bundled = resources + "/runtime/" + arch + "/bin/python3"
            if FileManager.default.isExecutableFile(atPath: bundled) { return bundled }
        }
        return "/usr/bin/python3"
    }
    func runSetup(_ arguments: [String], _ completion: @escaping ([String: Any]) -> Void) {
        guard let script = Bundle.main.path(forResource: "app_setup", ofType: "py") else {
            completion(["error": t("应用包缺少 app_setup.py", "app_setup.py missing from the app bundle")]); return
        }
        item.button?.appearsDisabled = true
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.setupPython())
            process.arguments = ["-B", script] + arguments
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
            let agents = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents")
            let installed = FileManager.default.fileExists(atPath: agents.appendingPathComponent("com.worldquant.wq-runner.plist").path)
                && FileManager.default.fileExists(atPath: agents.appendingPathComponent("com.worldquant.wq-menu.plist").path)
            if !installed {
                activateRow.title = t("安装调度与登录自启", "Install scheduling and login auto-start")
                activateRow.action = #selector(activate)
                activateRow.target = self
                activateRow.isEnabled = true
                activateRow.toolTip = t("这不是研究开关。它只安装两个登录项：每 60 秒唤醒一次调度，以及登录后打开这个菜单。研究的开始和停止用菜单里的那一个按钮。",
                                        "This is not the research switch. It only installs two login items: wake the scheduler every 60 seconds, and open this menu at login. Start and stop research with the button in the menu.")
                settingsMenu.insertItem(activateRow, at: 0)
                settingsMenu.insertItem(activateSep, at: 1)
            }
        }
    }
    var skipLaunchBecauseBusy = false
    func enterMainMode() {
        ready = true
        lockFD = open(root + "/var/run/menubar.lock", O_CREAT | O_RDWR, 0o600)
        guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else { NSApp.terminate(nil); return }
        buildMainMenu()
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        if !skipLaunchBecauseBusy { perform("launch") }
        perform("identity-refresh")
        perform("history"); perform("submissions"); perform("standby"); perform("notifications"); perform("settings")
        if timer == nil {
            timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in self?.refresh() }
        }
        if notifyTimer == nil {
            notifyTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.perform("notifications") }
        }
    }
    func detach(_ row: NSMenuItem) {
        // 同一 NSMenuItem 不能同时属于两个菜单；语言切换重建时若不先摘下，insertItem 会直接让进程 abort。
        if let owner = row.menu, owner.index(of: row) >= 0 {
            owner.removeItem(row)
        }
    }
    func buildMainMenu() {
        // 语言切换后整棵静态菜单按当前 lang 重建；动态数据由后续 fill* 回填。
        for row in [accountRow, brainRow, headline, detail, tick, nextAt, researchModel, reviewModel,
                    cycles, submissions, standby, launchRow, notifyRow, autoSubmitRow, submissionRow,
                    activateRow, activateSep, cycleRow, powerRow] {
            detach(row)
        }
        controls.removeAll()
        menu.removeAllItems()
        accountMenu = NSMenu(title: t("账号", "Account"))
        accountMenu.autoenablesItems = false
        textRow(brainRow, t("BRAIN 账号：读取中…", "BRAIN account: loading…"))
        accountMenu.addItem(brainRow)
        accountMenu.addItem(.separator())
        for (title, selector, tip) in [
            (t("刷新账号信息", "Refresh account"), #selector(refreshIdentity),
             t("向 BRAIN 读取一次等级和分数。打开应用、每天当地时间 8 点、提交成功时也会读一次，其余时间用上次记录。",
               "Reads level and score from BRAIN once. The app also reads on open, at 8:00 local time, and after a successful submission; otherwise it keeps the last record.")),
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
        accountRow.title = t("未登录", "Not signed in")
        accountRow.submenu = accountMenu
        accountRow.isEnabled = true
        menu.addItem(accountRow)
        for row in [headline, detail, tick, nextAt, researchModel, reviewModel] { row.isEnabled = false; menu.addItem(row) }
        menu.addItem(.separator())
        historyMenu = NSMenu(title: t("轮次历史", "Cycle history"))
        submissionsMenu = NSMenu(title: t("已提交 Alpha", "Submitted Alphas"))
        standbyMenu = NSMenu(title: t("备选 Alpha", "Standby Alphas"))
        for (row, submenu) in [(cycles, historyMenu), (submissions, submissionsMenu), (standby, standbyMenu)] {
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
        spendMenu = NSMenu(title: t("模型花费上限", "Model spend cap"))
        settingsMenu.autoenablesItems = false; settingsMenu.delegate = self
        launchRow.target = self
        launchRow.title = t("启动时开始自动研究", "Start automatic research on launch")
        launchRow.toolTip = t("打开菜单栏时，如果没有被你手动停止，就开始自动研究。点过停止，或退出时选择了停止，会保持到你再点开始。",
                              "On open, start automatic research unless you stopped it. Stop, or a quit option that stops tasks, stays off until you press Start.")
        launchRow.representedObject = "config=launch_research=off"
        launchRow.state = .on
        settingsMenu.addItem(launchRow)
        settingsMenu.addItem(notifyRow)
        submissionRow.toolTip = t("打开后，内部通过且授权仍有效的 Alpha 会自动入队；24 小时名额满了就进备选。真正提交前仍会重新做官方检查。",
                                  "When on, an Alpha that passes internal gates is queued automatically while authorization is valid. A full 24-hour cap becomes standby. The official check still runs again before any POST.")
        settingsMenu.addItem(submissionRow)
        settingsMenu.addItem(.separator())
        modelMenu = NSMenu(title: t("模型", "Models"))
        for (title, submenu, tip) in [
            (t("界面语言", "Interface language"), languageMenu,
             t("自动跟随系统：中文环境用中文，其余用英文；也可固定中文或英文。",
               "Automatic follows the system locale (Chinese for zh*, English otherwise); or fix Chinese / English.")),
            (t("模型", "Models"), modelMenu,
             t("保存多个自建模型，再分别指定下一轮的研究和审查。这两个位置必须不同。",
               "Save several self-hosted models, then choose the next cycle’s research and review models. Those two slots must differ.")),
            (t("路由预设", "Routing presets"), presetMenu,
             t("切换整套餐路：研究与审查的渠道顺序；下一项任务领取时生效，在途任务保持原路由。",
               "Switch the whole route set: provider order for research and review; applies to the next claimed task. In-flight tasks keep their routes.")),
            (t("渠道", "Providers"), providerMenu,
             t("临时停用或恢复单个渠道；不打断在途调用，预算闸门保留。",
               "Temporarily disable or re-enable one provider; in-flight calls are unaffected, budget gates remain.")),
            (t("模型花费上限", "Model spend cap"), spendMenu,
             t("已知模型花费达到该美元数后，停止新的模型调用。未知金额不记成 $0。",
               "Stops new model calls once known spend reaches this dollar amount. Unknown prices are not counted as $0.")),
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
            if submenu === languageMenu { languageItem = row }
            settingsMenu.addItem(row)
        }
        for (title, selector, tip) in [
            (t("检查更新", "Check for updates"), #selector(checkUpdate),
             t("对照官网版本。有新版本时打开下载页，不自动安装。",
               "Compares with the website version. Opens the download page when a newer build exists; nothing is installed automatically.")),
            (t("打开运行日志", "Open run log"), #selector(logs),
             t("查看本机调度日志。", "View the local scheduler log."))
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; settingsMenu.addItem(row); controls.append(row)
        }
        let settingsRow = NSMenuItem(title: t("设置", "Settings"), action: nil, keyEquivalent: "")
        settingsRow.submenu = settingsMenu; settingsRow.isEnabled = true
        menu.addItem(settingsRow)
        menu.addItem(.separator())
        cycleRow.target = self
        powerRow.target = self
        applyRunState(paused: true, enabled: false, cycleOpen: false)
        for row in [cycleRow, powerRow] { menu.addItem(row); controls.append(row) }
        for (title, selector, tip) in [
            (t("退出…", "Quit…"), #selector(quit),
             t("选择只关菜单、本轮结束后停止，或立刻结束本地任务。研究调度不依赖菜单是否开着。",
               "Choose to close the menu only, stop after this cycle, or stop local tasks now. The scheduler does not need this menu to stay open."))
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; menu.addItem(row); controls.append(row)
        }
        notifyRow.title = t("系统通知", "System notifications")
        notifyRow.target = self
        notifyRow.toolTip = t("Alpha 提交成功或任务失败时发送 macOS 系统通知；需在系统设置中允许本应用通知。",
                              "Sends macOS notifications when an Alpha is accepted or a task fails; allow notifications for this app in System Settings.")
        notifyRow.representedObject = "config=notifications=off"   // 默认开启，点击即关闭；fillSettings 会按实际状态刷新
        autoSubmitRow.isEnabled = false
        autoSubmitRow.title = t("自动入队说明：打开下方提交队列后，内部通过且授权仍有效的 Alpha 会自动入队；满额则成为备选。这里没有第二个开关。",
                                "Queue note: with the submission queue below on, an Alpha that passes internal gates is queued automatically while authorization is valid; a full cap becomes standby. There is no second switch.")
        submissionRow.title = t("提交队列", "Submission queue")
        submissionRow.target = self
        submissionRow.toolTip = t("打开后才允许 wq brain submit 入队。达标不会自动 POST，每个 Alpha 仍须单独验收。",
                                  "Arms wq brain submit. A passing Alpha is not posted automatically; each one still needs its own review.")
        submissionRow.representedObject = "config=submission=on"
        updateActivateRow()
    }
    func menuWillOpen(_ opened: NSMenu) {
        guard ready else { return }
        if opened === historyMenu { load("history") }
        else if opened === submissionsMenu { load("submissions") }
        else if opened === standbyMenu { load("standby") }
        else if opened === settingsMenu { updateActivateRow(); load("settings") }
        else if opened === menu { refresh() }
    }
    func load(_ kind: String) {
        if fetched[kind] == nil || Date().timeIntervalSince(fetched[kind]!) > 30 { perform(kind) }
    }
    func applyIcon(active: Bool) {
        // 模板图标本身是单色。暂停时再降低不透明度，和旁边常亮的菜单栏图标区分开。
        item.button?.alphaValue = active ? 1 : 0.4
    }
    func applyRunState(paused: Bool, enabled: Bool, cycleOpen: Bool) {
        let running = !paused && enabled
        powerStops = running
        powerRow.title = running ? t("停止自动研究", "Stop automatic research") : t("开始自动研究", "Start automatic research")
        powerRow.toolTip = running
            ? t("不再领取新任务；当前这一轮允许收尾。要立刻结束这一轮，用「取消当前轮次」。",
               "Stops claiming new tasks; the current cycle may finish. Use “Cancel current cycle” to end that cycle now.")
            : t("恢复队列，按既有间隔持续研究。", "Resumes the queue and keeps researching at the configured interval.")
        cycleCancels = cycleOpen
        if cycleOpen {
            cycleRow.isHidden = false
            cycleRow.title = t("取消当前轮次", "Cancel current cycle")
            cycleRow.toolTip = t("结束当前这一轮，并停止还没发到平台的本地任务。已经发出的模拟不会撤回。自动研究保持开启，下一轮仍按间隔开始。",
                                 "Ends the current cycle and stops local work that has not been sent. A simulation already sent to the platform is not withdrawn. Automatic research stays on; the next cycle follows the interval.")
        } else if paused {
            cycleRow.isHidden = true
        } else {
            cycleRow.isHidden = false
            cycleRow.title = t("立刻运行下一轮", "Run next cycle now")
            cycleRow.toolTip = t("跳过轮间等待。若当前没有轮次，马上开始一轮。额度、授权与平台冷却仍然生效。",
                                 "Skips the wait between cycles. Starts one immediately when none is running. Quota, authorization and platform cooldown still apply.")
        }
    }
    func refresh() { if !actionBusy { perform("status") } }
    @objc func refreshIdentity() { perform("identity-refresh", "manual") }
    func applyIdentity(_ identity: [String: Any]?) {
        let identity = identity ?? [:]
        accountRow.title = identity["title"] as? String ?? t("未登录", "Not signed in")
        let identityDetail = identity["detail"] as? String ?? ""
        if !identityDetail.isEmpty { textRow(brainRow, identityDetail) }
    }
    @objc func cycleAction() { perform(cycleCancels ? "cancel-cycle" : "run-next") }
    @objc func powerAction() { perform(powerStops ? "pause" : "start") }
    @objc func runNext() { perform("run-next") }
    @objc func start() { perform("start") }
    @objc func pause() { perform("pause") }
    @objc func quit() {
        let box = NSAlert()
        box.messageText = t("退出菜单栏", "Quit the menu bar")
        box.informativeText = t("研究由系统调度在后台继续，不依赖这个菜单是否开着。已经发到平台的模拟不会撤回。",
                                "Research keeps running in the background and does not need this menu. A simulation already sent to the platform is not withdrawn.")
        box.addButton(withTitle: t("仅退出", "Quit menu only"))
        box.addButton(withTitle: t("本轮结束后停止", "Stop after this cycle"))
        box.addButton(withTitle: t("立刻结束所有任务", "Stop all tasks now"))
        box.addButton(withTitle: t("取消", "Cancel"))
        NSApp.activate(ignoringOtherApps: true)
        switch box.runModal() {
        case .alertFirstButtonReturn:
            NSApp.terminate(nil)
        case .alertSecondButtonReturn:
            perform("quit-after-cycle")
        case .alertThirdButtonReturn:
            perform("quit-now")
        default:
            return
        }
    }
    @objc func brainLogin() { perform("brain-login") }
    @objc func brainCheck() { perform("brain-check") }
    @objc func brainRegister() { perform("brain-register") }
    @objc func logs() { NSWorkspace.shared.open(URL(fileURLWithPath: root + "/var/run/launchd.out.log")) }
    @objc func checkUpdate() { perform("update") }
    @objc func apply(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        let parts = code.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        if parts[0] == "preset", parts.count > 1 {
            // 切换预设前让用户选择：临时 N 轮（步进器可调，默认 1）还是永久切换。
            let box = NSAlert()
            box.messageText = t("切换路由预设：", "Switch routing preset: ") + parts[1]
            box.informativeText = t("临时切换：接下来 N 个新建研究轮次使用该预设（N 用上方步进器调整），用完自动恢复当前永久预设。\n永久切换：之后所有新任务都使用该预设。\n在途任务都保持原路由。",
                                    "Temporary: the next N new research cycles use this preset (set N with the stepper above), then the current permanent preset resumes.\nPermanent: all new tasks use it from now on.\nIn-flight tasks keep their routes either way.")
            let cyclesInput = PresetCyclesInput(alert: box, lang: lang)
            box.accessoryView = cyclesInput.row
            box.addButton(withTitle: cyclesInput.buttonTitle())
            box.addButton(withTitle: t("永久切换", "Permanent"))
            box.addButton(withTitle: t("取消", "Cancel"))
            NSApp.activate(ignoringOtherApps: true)
            switch box.runModal() {
            case .alertFirstButtonReturn: perform("preset-once", parts[1] + ":" + String(cyclesInput.value()))
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
        let target = kind == "history" ? historyMenu : (kind == "standby" ? standbyMenu : submissionsMenu)
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
                    : badge == "standby" ? .systemOrange
                    : badge == "failed" ? .systemRed
                    : badge == "progress" ? .systemBlue : nil
                let mark = badge == "submitted" ? "★ " : (badge == "standby" ? "◇ " : "")
                let row = block([mark + title] + lines, titleColor: color)
                row.toolTip = (entry["detail"] as? [String] ?? lines).joined(separator: "\n")
                row.view?.toolTip = row.toolTip
                target.addItem(row)
                target.addItem(.separator())
            }
        } else {
            if kind == "standby" {
                standby.title = t("备选 Alpha（\(entries.count)）", "Standby Alphas (\(entries.count))")
            } else {
                submissions.title = t("已提交 Alpha（\(entries.count)）", "Submitted Alphas (\(entries.count))")
            }
            for entry in entries {
                let badge = entry["badge"] as? String ?? ""
                let title = (badge == "submitted" ? "★ " : (badge == "standby" ? "◇ " : "")) + (entry["title"] as? String ?? "")
                let row = NSMenuItem(title: title, action: nil, keyEquivalent: "")
                if badge == "standby" {
                    row.attributedTitle = NSAttributedString(string: title, attributes: [
                        .foregroundColor: NSColor.systemOrange,
                        .font: NSFont.systemFont(ofSize: 13, weight: .semibold)])
                } else if badge == "submitted" {
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
        autoSubmitRow.title = t("自动入队说明：打开下方提交队列后，内部通过且授权仍有效的 Alpha 会自动入队；满额则成为备选。这里没有第二个开关。",
                                "Queue note: with the submission queue below on, an Alpha that passes internal gates is queued automatically while authorization is valid; a full cap becomes standby. There is no second switch.")
        let submissionOn = response["submission_enabled"] as? Bool ?? false
        submissionRow.state = submissionOn ? .on : .off
        submissionRow.representedObject = "config=submission=" + (submissionOn ? "off" : "on")
        let launchOn = response["launch_research"] as? Bool ?? true
        launchRow.state = launchOn ? .on : .off
        launchRow.representedObject = "config=launch_research=" + (launchOn ? "off" : "on")
        launchRow.title = t("启动时开始自动研究", "Start automatic research on launch")
        let setting = response["language_setting"] as? String ?? "auto"
        let languageName = setting == "zh" ? "中文" : (setting == "en" ? "English" : t("跟随系统", "Follow system"))
        languageItem.title = t("界面语言", "Interface language") + " · " + languageName
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
        let onceCycles = response["preset_once_cycles"] as? Int ?? 1
        let cyclePreset = response["cycle_preset"] as? String ?? ""
        var head = t("永久预设：", "Permanent preset: ") + permanent
        if !cyclePreset.isEmpty { head += t("｜本轮临时：", " | this cycle: ") + cyclePreset }
        if !once.isEmpty { head += t("｜临时待用：", " | pending: ") + once + " ×" + String(onceCycles) }
        presetMenu.addItem(info(head))
        presetMenu.addItem(info(t("点击预设后选择：临时 N 轮（可调）/ 永久切换", "Click a preset to choose: temporary N cycles (adjustable) / permanent")))
        if !once.isEmpty {
            let cancelRow = NSMenuItem(title: t("取消临时切换（\(once) ×\(onceCycles)）",
                                                "Cancel temporary switch (\(once) ×\(onceCycles))"),
                                       action: #selector(apply(_:)), keyEquivalent: "")
            cancelRow.target = self; cancelRow.representedObject = "preset-cancel"
            presetMenu.addItem(cancelRow)
        }
        presetMenu.addItem(NSMenuItem.separator())
        for preset in response["presets"] as? [[String: Any]] ?? [] {
            let name = preset["name"] as? String ?? ""
            let research = preset["research"] as? String ?? ""
            let review = preset["review"] as? String ?? ""
            let row = NSMenuItem(title: name, action: #selector(apply(_:)), keyEquivalent: "")
            row.target = self
            row.representedObject = "preset=" + name
            row.state = name == active ? .on : .off
            row.toolTip = preset["routes"] as? String ?? ""
            if !research.isEmpty || !review.isEmpty {
                let caption = NSMutableAttributedString(string: name + "\n", attributes: [
                    .font: NSFont.systemFont(ofSize: 13, weight: .semibold),
                    .foregroundColor: NSColor.labelColor])
                caption.append(NSAttributedString(
                    string: t("研究  ", "Research  ") + research + "\n" + t("审查  ", "Review  ") + review,
                    attributes: [
                        .font: NSFont.systemFont(ofSize: 11),
                        .foregroundColor: NSColor.secondaryLabelColor]))
                row.attributedTitle = caption
            }
            presetMenu.addItem(row)
        }
        providerMenu.removeAllItems()
        modelMenu.removeAllItems()
        let addProvider = NSMenuItem(title: t("添加自定义模型…", "Add a custom model…"), action: #selector(addCustomProvider), keyEquivalent: "")
        addProvider.target = self
        addProvider.toolTip = t("保存一个 OpenAI 或 Anthropic 兼容接口。添加后在下面的研究和审查里选用。",
                                "Save an OpenAI- or Anthropic-compatible endpoint. Then choose it for research or review below.")
        modelMenu.addItem(addProvider)
        modelMenu.addItem(info(t("研究和审查必须不同，从下一轮生效。", "Research and review must differ. The choice applies from the next cycle.")))
        let researchHead = response["research_provider"] as? String ?? ""
        let reviewHead = response["review_provider"] as? String ?? ""
        let models = response["providers"] as? [[String: Any]] ?? []
        for (role, head, title) in [("research", researchHead, t("研究模型", "Research model")),
                                    ("review", reviewHead, t("审查模型", "Review model"))] {
            modelMenu.addItem(.separator())
            modelMenu.addItem(info(title))
            for provider in models {
                let name = provider["name"] as? String ?? ""
                pickRow(provider["label"] as? String ?? name, "provider-role=\(role):\(name)", on: name == head,
                        tip: t("下一轮这个角色优先用它。当前这一轮不变。", "The next cycle prefers this model for the role. The current cycle stays as it is."),
                        in: modelMenu)
            }
        }
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
        spendMenu.removeAllItems()
        let known = response["spend_known_usd"] as? Double ?? 0
        let unknown = response["spend_unknown_calls"] as? Int ?? 0
        let capNumber = response["spend_cap_usd"] as? Double
        spendMenu.addItem(info(String(format: t("自本次设置起已知花费 $%.4f", "Known spend since this cap was set: $%.4f"), known)))
        if unknown > 0 {
            spendMenu.addItem(info(t("另有 \(unknown) 次调用金额未知，未计入，也不记成 $0",
                                    "\(unknown) calls have an unknown price; they are omitted and not treated as $0")))
        }
        pickRow(t("不限", "No cap"), "config=model_spend_cap_usd=none", on: capNumber == nil,
                tip: t("不按美元合计拦截模型调用", "Do not block model calls by a dollar total"), in: spendMenu)
        let presets = (response["spend_presets"] as? [NSNumber])?.map { $0.intValue } ?? [5, 10, 20, 50, 100]
        for value in presets {
            let matched = capNumber != nil && abs(capNumber! - Double(value)) < 0.001
            pickRow("$\(value)", "config=model_spend_cap_usd=\(value)", on: matched,
                    tip: t("从现在起，已知花费达到 $\(value) 后停止新的模型调用",
                           "From now, stop new model calls once known spend reaches $\(value)"), in: spendMenu)
        }
        if let capNumber, !presets.contains(where: { abs(capNumber - Double($0)) < 0.001 }) {
            spendMenu.addItem(info(String(format: t("当前上限 $%.4f", "Current cap $%.4f"), capNumber)))
        }
        fetched["settings"] = Date()
    }

    @objc func addCustomProvider() {
        let box = NSAlert()
        box.messageText = t("添加自定义模型", "Add a custom model")
        box.informativeText = t("保存成一个可切换的模板。可以添加多个。地址用 https；本机和内网可以用 http。密钥只留在本机私有目录。添加后到「设置 → 模型」里分别选研究和审查，这两个位置必须不同。",
                                "Save it as a switchable template. You can add several. Use https; http is allowed on this machine and on a private network. The key stays in a local private file. Then choose research and review under Settings → Models; those two slots must differ.")
        let form = CustomProviderForm(lang: lang)
        box.accessoryView = form.view
        box.addButton(withTitle: t("添加", "Add"))
        box.addButton(withTitle: t("取消", "Cancel"))
        NSApp.activate(ignoringOtherApps: true)
        guard box.runModal() == .alertFirstButtonReturn else { return }
        guard let spec = form.spec() else {
            alert(t("还不能添加", "Cannot add it yet"), t("名称、协议、地址和模型 ID 都要填写。", "Name, protocol, address and model ID are all required."))
            return
        }
        var env: [String: String] = [:]
        if let key = form.key(), !key.isEmpty { env["WQ_PROVIDER_KEY"] = key }
        perform("provider-add", spec, env: env)
    }
    func perform(_ action: String, _ arg: String? = nil, env: [String: String] = [:]) {
        let readOnly = ["status", "history", "submissions", "standby", "settings", "notifications", "identity-refresh"].contains(action)
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
            for (key, value) in env { environment[key] = value }
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
                    if action == "identity-refresh" { return }
                    if action == "history" || action == "submissions" || action == "standby" {
                        let target = action == "history" ? self.historyMenu : (action == "standby" ? self.standbyMenu : self.submissionsMenu)
                        target.removeAllItems(); target.addItem(self.block([error])); return
                    }
                    self.fetched["settings"] = nil
                    self.textRow(self.headline, self.t("读取失败", "Read failed"))
                    self.textRow(self.detail, String(error.prefix(90)))
                    self.item.button?.toolTip = error
                    if action == "launch" {
                        self.refresh()
                    } else if !readOnly {
                        let alert = NSAlert(); alert.messageText = self.t("操作未完成", "Action failed"); alert.informativeText = error; alert.runModal()
                    }
                } else if action == "quit-after-cycle" || action == "quit-now" { NSApp.terminate(nil)
                } else if action == "history" || action == "submissions" || action == "standby" { self.fill(action, response)
                } else if action == "update" {
                    let message = response["message"] as? String ?? self.t("检查更新失败", "Update check failed")
                    self.alert("WorldQuant", message)
                    if response["update"] as? Bool == true, let raw = response["url"] as? String, let link = URL(string: raw) {
                        NSWorkspace.shared.open(link)
                    }
                } else if action == "settings" { self.fillSettings(response)
                } else if action == "notifications" { self.postNotifications(response)
                } else if action == "identity-refresh" {
                    self.applyIdentity(response["identity"] as? [String: Any])
                    if let message = response["message"] as? String, !message.isEmpty {
                        self.alert("WorldQuant", message)
                    }
                } else if action == "status" {
                    if self.actionBusy { return }
                    let identity = response["identity"] as? [String: Any] ?? [:]
                    self.applyIdentity(identity)
                    self.textRow(self.headline, response["title"] as? String ?? self.t("状态未知", "Status unknown"))
                    self.textRow(self.detail, String((response["message"] as? String ?? "").prefix(90)))
                    self.cycles.title = self.t("轮次历史", "Cycle history")
                    self.cycles.toolTip = response["cycles"] as? String ?? ""
                    self.textRow(self.tick, self.t("最近调度：", "Last tick: ") + (response["last_tick"] as? String ?? self.t("尚无记录", "No records yet")))
                    self.textRow(self.nextAt, self.t("下一轮：", "Next cycle: ") + (response["next_at"] as? String ?? self.t("待当前任务完成／调度检查", "awaiting current task / scheduler check")))
                    if (identity["detail"] as? String ?? "").isEmpty {
                        self.textRow(self.brainRow, (response["brain_bound"] as? Bool) == true
                                     ? self.t("已登录", "Signed in")
                                     : self.t("未登录；用「绑定 / 重新登录」", "Not signed in; use “Bind / re-login”"))
                    }
                    let models = response["next_models"] as? [[String: Any]] ?? []
                    for (index, row) in [self.researchModel, self.reviewModel].enumerated() {
                        self.textRow(row, self.t("下轮", "Next: ") + (index < models.count ? models[index]["title"] as? String ?? self.t("未知", "unknown") : self.t("未知", "unknown")))
                        row.toolTip = self.t("预估下一轮路由；任务领取时冻结，重试可能切换备用渠道。",
                                             "Estimated routing for the next cycle; frozen at claim time, retries may switch to a fallback provider.")
                    }
                    let paused = response["paused"] as? Bool == true
                    let enabled = response["enabled"] as? Bool == true
                    let cycleOpen = response["cycle_open"] as? Bool == true
                    self.applyRunState(paused: paused, enabled: enabled, cycleOpen: cycleOpen)
                    self.applyIcon(active: !paused && enabled)
                    self.item.button?.toolTip = self.headline.title + "\n" + self.detail.title
                } else if action.hasPrefix("brain-") {
                    if let message = response["message"] as? String, !message.isEmpty {
                        self.alert("WorldQuant", message)
                    }
                    self.refresh()
                } else {
                    let quiet = ["start", "pause", "run-next", "cancel-cycle", "config", "launch",
                                 "quit-after-cycle", "quit-now", "provider-add", "provider-role",
                                 "preset", "preset-once", "preset-cancel", "provider"].contains(action)
                    if let message = response["message"] as? String, !message.isEmpty {
                        self.textRow(self.detail, String(message.prefix(90)))
                        if !quiet { self.alert("WorldQuant", message) }
                    }
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
