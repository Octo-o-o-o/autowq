import AppKit
import CoreFoundation
import Darwin
import UserNotifications

/// 界面设计规范：菜单行与研究进展窗口共用这一套间距、字体与颜色，调整只在这里改。
/// 间距取 4pt 网格；字体分 窗口标题 / 区块标题 / 正文 / 辅助信息 / 说明 五档；
/// 长文本一律折行（preferredMaxLayoutWidth），不截断。
enum UI {
    // 菜单
    static let menuWidth: CGFloat = 380      // 菜单统一宽度
    static let menuPadX: CGFloat = 16        // 菜单行水平内边距
    static let menuRowPadY: CGFloat = 6      // 状态行垂直内边距
    static let menuBlockPadY: CGFloat = 10   // 卡片式菜单行垂直内边距
    static let titleBodyGap: CGFloat = 4     // 标题段落后与正文的间距
    // 窗口
    static let windowPad: CGFloat = 20       // 窗口内容边距
    static let headerPadY: CGFloat = 12      // 工具行垂直间距
    static let cardGap: CGFloat = 12         // 卡片间距
    static let cardPad: CGFloat = 14         // 卡片内边距
    static let cardTitleGap: CGFloat = 6     // 卡片标题与正文间距
    static let cardRadius: CGFloat = 10
    static let controlGap: CGFloat = 8       // 表单行内、按钮间距
    // 字体
    static let fontWindowTitle = NSFont.systemFont(ofSize: 15, weight: .semibold)
    static let fontTitle = NSFont.systemFont(ofSize: 13, weight: .semibold)
    static let fontBody = NSFont.systemFont(ofSize: 13)
    static let fontMeta = NSFont.systemFont(ofSize: 12)
    static let fontCaption = NSFont.systemFont(ofSize: 11)
    // 语义色（徽章）：提交=绿 备选=橙 失败=红 进行中=蓝
    static func badgeColor(_ badge: String) -> NSColor? {
        switch badge {
        case "submitted": return .systemGreen
        case "standby": return .systemOrange
        case "failed": return .systemRed
        case "progress": return .systemBlue
        default: return nil
        }
    }
}

/// 折行标签：宽度由 Auto Layout 决定后回写 preferredMaxLayoutWidth，保证高度按实际宽度计算。
final class WrapLabel: NSTextField {
    override func layout() {
        super.layout()
        if preferredMaxLayoutWidth != bounds.width {
            preferredMaxLayoutWidth = bounds.width
        }
    }
}

/// 滚动文档容器：NSView 默认非翻转，内容不足一屏时会贴底；翻转让列表始终从顶部排布。
final class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

/// 预设切换对话框里的临时轮数输入（标签 + 数字框 + 步进器）；确认按钮标题随轮数实时更新。
/// NSAlert 的 accessoryView 区域对 NSStackView 的固有尺寸支持不可靠（标签会被压没），这里用固定 frame 布局。
final class PresetCyclesInput: NSObject, NSTextFieldDelegate {
    static let maxCycles = 100
    let alert: NSAlert
    let lang: String
    let field = NSTextField(string: "1")
    let stepper = NSStepper()
    let row = NSView(frame: NSRect(x: 0, y: 0, width: 220, height: 26))
    weak var onceButton: NSButton?

    init(alert: NSAlert, lang: String) {
        self.alert = alert
        self.lang = lang
        super.init()
        let formatter = NumberFormatter()
        formatter.minimum = 1 as NSNumber; formatter.maximum = Self.maxCycles as NSNumber; formatter.allowsFloats = false
        field.formatter = formatter
        field.alignment = .center
        field.font = .monospacedDigitSystemFont(ofSize: 13, weight: .regular)
        field.frame = NSRect(x: 72, y: 1, width: 48, height: 24)
        field.delegate = self
        field.target = self; field.action = #selector(changed(_:))
        stepper.minValue = 1; stepper.maxValue = Double(Self.maxCycles); stepper.integerValue = 1; stepper.valueWraps = false
        stepper.frame = NSRect(x: 72 + 48 + UI.controlGap, y: 0, width: 20, height: 26)
        stepper.target = self; stepper.action = #selector(changed(_:))
        let label = NSTextField(labelWithString: lang == "zh" ? "临时轮数" : "Cycles")
        label.frame = NSRect(x: 0, y: 4, width: 64, height: 17)
        row.addSubview(label)
        row.addSubview(field)
        row.addSubview(stepper)
    }
    func value() -> Int { max(1, min(Self.maxCycles, field.integerValue)) }
    func buttonTitle(for n: Int) -> String {
        let shown = max(1, min(Self.maxCycles, n))
        return lang == "zh" ? (shown <= 1 ? "仅切换一轮" : "临时切换 \(shown) 轮")
                            : (shown <= 1 ? "One cycle only" : "Temporary for \(shown) cycles")
    }
    func buttonTitle() -> String { buttonTitle(for: value()) }
    func showCount(_ n: Int) {
        onceButton?.title = buttonTitle(for: n)
    }
    func controlTextDidChange(_ obj: Notification) {
        guard let n = Int(field.stringValue), (1...Self.maxCycles).contains(n) else { return }
        stepper.integerValue = n
        showCount(n)
    }
    @objc private func changed(_ sender: Any?) {
        if sender as? NSStepper === stepper {
            field.stringValue = String(stepper.integerValue)
        } else {
            stepper.integerValue = value()
            field.integerValue = value()
        }
        showCount(sender as? NSStepper === stepper ? stepper.integerValue : value())
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
        view.spacing = UI.controlGap
        view.setFrameSize(NSSize(width: 380, height: 200))
        protocolPopup.addItems(withTitles: ["openai", "anthropic"])
        noKey.title = zh ? "这个服务不需要 API Key" : "This service does not need an API key"
        func row(_ label: String, _ field: NSView) -> NSStackView {
            let line = NSStackView()
            line.orientation = .horizontal
            line.spacing = UI.controlGap
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

/// A native form backed by the same setting descriptors as the execution gates.
/// Drafts survive category changes and refreshes; a save submits one validated batch.
final class SettingEditorRow: NSView, NSTextFieldDelegate {
    let entry: [String: Any]
    let field = NSTextField(string: "")
    let choice = NSPopUpButton()
    let toggle = NSButton(checkboxWithTitle: "", target: nil, action: nil)
    let date = NSDatePicker()
    let nullable = NSButton(checkboxWithTitle: "", target: nil, action: nil)
    weak var pane: SettingsPane?
    let kind: String
    var key: String { entry["key"] as? String ?? "" }
    var input: String {
        if kind == "boolean" { return toggle.state == .on ? "true" : "false" }
        if kind == "choice" { return choice.selectedItem?.representedObject as? String ?? "" }
        if kind == "deadline" { return ISO8601DateFormatter().string(from: date.dateValue) }
        if entry["nullable"] as? Bool == true && nullable.state == .on { return "none" }
        return field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    init(entry: [String: Any], value: String, pane: SettingsPane) {
        self.entry = entry; self.pane = pane; kind = entry["type"] as? String ?? "integer"
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        let title = WrapLabel(wrappingLabelWithString: entry["label"] as? String ?? "")
        title.font = .systemFont(ofSize: 13, weight: .medium)
        let note = WrapLabel(wrappingLabelWithString: pane.explanation(entry))
        note.font = .systemFont(ofSize: 11); note.textColor = .secondaryLabelColor
        let labels = NSStackView(views: [title, note]); labels.orientation = .vertical; labels.alignment = .leading; labels.spacing = 5
        let controls = NSStackView(); controls.orientation = .vertical; controls.alignment = .trailing; controls.spacing = 6
        switch kind {
        case "boolean":
            toggle.title = pane.t("开启", "Enabled"); toggle.state = value == "true" ? .on : .off
            toggle.target = self; toggle.action = #selector(changed); controls.addArrangedSubview(toggle)
        case "choice":
            for option in entry["choices"] as? [[String: String]] ?? [] {
                choice.addItem(withTitle: option["label"] ?? "")
                choice.lastItem?.representedObject = option["value"]
                if option["value"] == value { choice.select(choice.lastItem) }
            }
            choice.target = self; choice.action = #selector(changed); controls.addArrangedSubview(choice)
        case "deadline":
            date.datePickerStyle = .textFieldAndStepper; date.datePickerElements = [.yearMonthDay, .hourMinuteSecond]
            date.timeZone = .current
            date.dateValue = ISO8601DateFormatter().date(from: value) ?? Date()
            date.target = self; date.action = #selector(changed); controls.addArrangedSubview(date)
            let zone = NSTextField(labelWithString: TimeZone.current.identifier); zone.font = .systemFont(ofSize: 10); zone.textColor = .secondaryLabelColor
            controls.addArrangedSubview(zone)
        default:
            field.stringValue = value == "none" ? "" : value
            field.alignment = .right; field.font = .monospacedDigitSystemFont(ofSize: 13, weight: .regular)
            field.placeholderString = pane.t("输入数值", "Enter value")
            field.delegate = self; field.widthAnchor.constraint(equalToConstant: 136).isActive = true
            controls.addArrangedSubview(field)
            if entry["nullable"] as? Bool == true {
                nullable.title = entry["empty_label"] as? String ?? pane.t("不限", "No cap")
                nullable.state = value == "none" ? .on : .off; field.isEnabled = nullable.state == .off
                nullable.target = self; nullable.action = #selector(changed); controls.addArrangedSubview(nullable)
            }
        }
        let row = NSStackView(views: [labels, controls]); row.orientation = .horizontal; row.alignment = .top; row.spacing = 24
        row.translatesAutoresizingMaskIntoConstraints = false; addSubview(row)
        labels.setContentHuggingPriority(.defaultLow, for: .horizontal)
        labels.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        controls.setContentHuggingPriority(.required, for: .horizontal)
        controls.widthAnchor.constraint(greaterThanOrEqualToConstant: 160).isActive = true
        NSLayoutConstraint.activate([
            row.leadingAnchor.constraint(equalTo: leadingAnchor), row.trailingAnchor.constraint(equalTo: trailingAnchor),
            row.topAnchor.constraint(equalTo: topAnchor, constant: 12), row.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -12),
            labels.widthAnchor.constraint(greaterThanOrEqualToConstant: 220),
        ])
        for control in [field, choice, toggle, date, nullable] as [NSControl] {
            control.setAccessibilityLabel(entry["label"] as? String ?? key)
            control.identifier = NSUserInterfaceItemIdentifier(key)
        }
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    @objc func changed() { field.isEnabled = nullable.state == .off; pane?.changed(self) }
    func controlTextDidChange(_ notification: Notification) { pane?.changed(self) }
}

final class SettingsPane: NSObject, NSWindowDelegate, NSSearchFieldDelegate {
    weak var owner: AppDelegate?
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 960, height: 700), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
    let sidebar = NSStackView()
    let search = NSSearchField()
    let heading = NSTextField(labelWithString: "")
    let subtitle = WrapLabel(wrappingLabelWithString: "")
    let body = NSStackView()
    let notice = WrapLabel(wrappingLabelWithString: "")
    let save = NSButton(title: "", target: nil, action: nil)
    let revert = NSButton(title: "", target: nil, action: nil)
    let cancelPending = NSButton(title: "", target: nil, action: nil)
    let advanced = NSButton(checkboxWithTitle: "", target: nil, action: nil)
    var snapshot: [String: Any] = [:]
    var drafts: [String: String] = [:]
    var originals: [String: String] = [:]
    var editors: [SettingEditorRow] = []
    var section = "general"
    var busy = false
    var lang: String { owner?.lang ?? "zh" }
    func t(_ zh: String, _ en: String) -> String { lang == "zh" ? zh : en }
    var groups: [(String, String, String)] { [
        ("general", t("通用", "General"), "gearshape"),
        ("research", t("研究与额度", "Research & limits"), "chart.xyaxis.line"),
        ("scheduling", t("运行与泳道", "Scheduling & lanes"), "square.stack.3d.up"),
        ("models", t("模型与路由", "Models & routing"), "point.3.connected.trianglepath.dotted"),
        ("simulation", t("模拟与提交", "Simulation & submission"), "arrow.up.doc"),
        ("providers", t("调用与花费", "Calls & spending"), "gauge"),
        ("authorization", t("授权期限", "Authorization"), "calendar.badge.clock"),
        ("support", t("帮助与诊断", "Help & diagnostics"), "questionmark.circle")
    ] }
    init(owner: AppDelegate) {
        self.owner = owner; super.init()
        window.delegate = self; window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 880, height: 580); window.setFrameAutosaveName("WorldQuantSettings")
        window.title = "WorldQuant"; window.titleVisibility = .hidden; window.titlebarAppearsTransparent = true
        guard let content = window.contentView else { return }
        let side = NSVisualEffectView(); side.material = .sidebar; side.blendingMode = .behindWindow; side.state = .followsWindowActiveState
        side.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(side)
        sidebar.orientation = .vertical; sidebar.alignment = .leading; sidebar.spacing = 5; sidebar.translatesAutoresizingMaskIntoConstraints = false; side.addSubview(sidebar)
        search.placeholderString = t("搜索设置", "Search settings"); search.delegate = self; search.sendsSearchStringImmediately = true
        search.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(search)
        heading.font = .systemFont(ofSize: 22, weight: .semibold)
        subtitle.font = .systemFont(ofSize: 12); subtitle.textColor = .secondaryLabelColor
        let title = NSStackView(views: [heading, subtitle]); title.orientation = .vertical; title.alignment = .leading; title.spacing = 6
        title.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(title)
        advanced.target = self; advanced.action = #selector(toggleAdvanced); advanced.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(advanced)
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true; scroll.drawsBackground = false; scroll.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(scroll)
        let document = FlippedView(); document.translatesAutoresizingMaskIntoConstraints = false; scroll.documentView = document
        body.orientation = .vertical; body.alignment = .leading; body.spacing = 0; body.translatesAutoresizingMaskIntoConstraints = false; document.addSubview(body)
        let footer = NSView(); footer.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(footer)
        let line = NSBox(); line.boxType = .separator; line.translatesAutoresizingMaskIntoConstraints = false; footer.addSubview(line)
        notice.font = .systemFont(ofSize: 11); notice.textColor = .secondaryLabelColor; notice.translatesAutoresizingMaskIntoConstraints = false; footer.addSubview(notice)
        save.bezelStyle = .rounded; save.target = self; save.action = #selector(saveChanges); save.keyEquivalent = "s"; save.keyEquivalentModifierMask = [.command]
        revert.bezelStyle = .rounded; revert.target = self; revert.action = #selector(revertChanges)
        cancelPending.bezelStyle = .rounded; cancelPending.target = self; cancelPending.action = #selector(cancelSaved)
        let buttons = NSStackView(views: [cancelPending, revert, save]); buttons.spacing = 8; buttons.translatesAutoresizingMaskIntoConstraints = false; footer.addSubview(buttons)
        NSLayoutConstraint.activate([
            side.leadingAnchor.constraint(equalTo: content.leadingAnchor), side.topAnchor.constraint(equalTo: content.topAnchor), side.bottomAnchor.constraint(equalTo: content.bottomAnchor), side.widthAnchor.constraint(equalToConstant: 188),
            sidebar.leadingAnchor.constraint(equalTo: side.leadingAnchor, constant: 12), sidebar.trailingAnchor.constraint(equalTo: side.trailingAnchor, constant: -12), sidebar.topAnchor.constraint(equalTo: side.topAnchor, constant: 22),
            search.leadingAnchor.constraint(equalTo: side.trailingAnchor, constant: 28), search.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -28), search.topAnchor.constraint(equalTo: content.topAnchor, constant: 18),
            title.topAnchor.constraint(equalTo: search.bottomAnchor, constant: 24), title.leadingAnchor.constraint(equalTo: search.leadingAnchor), title.trailingAnchor.constraint(equalTo: search.trailingAnchor),
            advanced.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 14), advanced.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            scroll.topAnchor.constraint(equalTo: advanced.bottomAnchor, constant: 10), scroll.leadingAnchor.constraint(equalTo: title.leadingAnchor), scroll.trailingAnchor.constraint(equalTo: title.trailingAnchor), scroll.bottomAnchor.constraint(equalTo: footer.topAnchor),
            document.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor), document.trailingAnchor.constraint(equalTo: scroll.contentView.trailingAnchor), document.topAnchor.constraint(equalTo: scroll.contentView.topAnchor),
            body.leadingAnchor.constraint(equalTo: document.leadingAnchor), body.trailingAnchor.constraint(equalTo: document.trailingAnchor), body.topAnchor.constraint(equalTo: document.topAnchor), body.bottomAnchor.constraint(equalTo: document.bottomAnchor),
            footer.leadingAnchor.constraint(equalTo: side.trailingAnchor), footer.trailingAnchor.constraint(equalTo: content.trailingAnchor), footer.bottomAnchor.constraint(equalTo: content.bottomAnchor), footer.heightAnchor.constraint(equalToConstant: 100),
            line.topAnchor.constraint(equalTo: footer.topAnchor), line.leadingAnchor.constraint(equalTo: footer.leadingAnchor), line.trailingAnchor.constraint(equalTo: footer.trailingAnchor),
            notice.leadingAnchor.constraint(equalTo: footer.leadingAnchor, constant: 28), notice.trailingAnchor.constraint(equalTo: footer.trailingAnchor, constant: -28), notice.topAnchor.constraint(equalTo: footer.topAnchor, constant: 10),
            buttons.trailingAnchor.constraint(equalTo: footer.trailingAnchor, constant: -24), buttons.bottomAnchor.constraint(equalTo: footer.bottomAnchor, constant: -16),
        ])
        window.center(); render()
    }
    func raw(_ value: Any?) -> String {
        guard let value, !(value is NSNull) else { return "none" }
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return number.boolValue ? "true" : "false" }
            return number.stringValue
        }
        return String(describing: value)
    }
    func shown(_ value: Any?) -> String {
        guard let value, !(value is NSNull) else { return t("未设置", "Not set") }
        if let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() {
            let formatter = NumberFormatter(); formatter.maximumFractionDigits = 4
            return formatter.string(from: number) ?? number.stringValue
        }
        return raw(value)
    }
    func explanation(_ entry: [String: Any]) -> String {
        var pieces: [String] = []
        if let used = entry["used"], !(used is NSNull) { pieces.append(t("已用 ", "Used ") + shown(used)) }
        if entry["has_pending"] as? Bool == true { pieces.append(t("当前 ", "Current ") + shown(entry["value"]) + t("；待应用 ", "; pending ") + shown(entry["pending"])) }
        if entry["active"] as? Bool == false { pieces.append(t("当前模式不使用此项", "Inactive in the current mode")) }
        if let min = entry["min"], let max = entry["max"] { pieces.append(shown(min) + "–" + shown(max)) }
        if let rule = entry["application"] as? String { pieces.append(rule) }
        if let note = entry["note"] as? String, !note.isEmpty { pieces.append(note) }
        return pieces.joined(separator: " · ")
    }
    func receive(_ response: [String: Any]) {
        snapshot = response
        let entries = (response["operating"] as? [String: Any])?["entries"] as? [[String: Any]] ?? []
        for entry in entries {
            let key = entry["key"] as? String ?? ""
            if drafts[key] == nil { originals[key] = raw(entry[entry["has_pending"] as? Bool == true ? "pending" : "value"]) }
        }
        render()
    }
    func open() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    func add(_ view: NSView) {
        body.addArrangedSubview(view); view.widthAnchor.constraint(equalTo: body.widthAnchor).isActive = true
    }
    func text(_ value: String, title: Bool = false) {
        let label = WrapLabel(wrappingLabelWithString: value); label.font = .systemFont(ofSize: title ? 14 : 12, weight: title ? .semibold : .regular)
        label.textColor = title ? .labelColor : .secondaryLabelColor
        let spacer = NSStackView(views: [label]); spacer.edgeInsets = NSEdgeInsets(top: 12, left: 0, bottom: 12, right: 0); add(spacer)
    }
    func command(_ label: String, code: String) {
        let button = NSButton(title: label, target: self, action: #selector(commandClicked(_:)))
        button.bezelStyle = .rounded; button.identifier = NSUserInterfaceItemIdentifier(code)
        let row = NSStackView(views: [button]); row.edgeInsets = NSEdgeInsets(top: 6, left: 0, bottom: 6, right: 0); add(row)
    }
    func menuControl(_ label: String, menu: NSMenu) {
        let name = NSTextField(labelWithString: label); name.font = .systemFont(ofSize: 13)
        let button = NSPopUpButton(frame: .zero, pullsDown: true)
        let copy = menu.copy() as! NSMenu
        copy.insertItem(withTitle: t("选择…", "Choose…"), action: nil, keyEquivalent: "", at: 0)
        button.menu = copy; button.widthAnchor.constraint(equalToConstant: 270).isActive = true
        let row = NSStackView(views: [name, button]); row.distribution = .equalSpacing
        row.edgeInsets = NSEdgeInsets(top: 8, left: 0, bottom: 8, right: 0); add(row)
    }
    func render() {
        for stack in [sidebar, body] { for view in stack.arrangedSubviews { stack.removeArrangedSubview(view); view.removeFromSuperview() } }
        editors = []
        let brand = NSTextField(labelWithString: "WorldQuant"); brand.font = .systemFont(ofSize: 16, weight: .semibold)
        sidebar.addArrangedSubview(brand)
        let caption = NSTextField(labelWithString: t("设置", "Settings")); caption.font = .systemFont(ofSize: 12); caption.textColor = .secondaryLabelColor; sidebar.addArrangedSubview(caption)
        sidebar.setCustomSpacing(22, after: caption)
        for (key, title, icon) in groups {
            let button = NSButton(title: title, target: self, action: #selector(selectSection(_:)))
            button.bezelStyle = .recessed; button.setButtonType(.pushOnPushOff); button.state = section == key ? .on : .off
            button.alignment = .left; button.image = NSImage(systemSymbolName: icon, accessibilityDescription: nil); button.imagePosition = .imageLeading
            button.identifier = NSUserInterfaceItemIdentifier(key); button.font = .systemFont(ofSize: 13)
            sidebar.addArrangedSubview(button); button.widthAnchor.constraint(equalTo: sidebar.widthAnchor).isActive = true; button.heightAnchor.constraint(equalToConstant: 32).isActive = true
        }
        let query = search.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        heading.stringValue = query.isEmpty ? (groups.first { $0.0 == section }?.1 ?? "") : t("搜索结果", "Search results")
        subtitle.stringValue = t("保存前可修改多项；累计用量和授权边界始终保留。", "Edit multiple values before saving. Usage counters and authorization boundaries are preserved.")
        search.placeholderString = t("搜索设置", "Search settings")
        advanced.title = t("显示高级参数", "Show advanced settings")
        advanced.isHidden = !query.isEmpty || ["models", "general", "authorization", "support"].contains(section)
        let data = snapshot["operating"] as? [String: Any] ?? [:]
        let entries = data["entries"] as? [[String: Any]] ?? []
        let filtered = entries.filter { entry in
            if !query.isEmpty { return [entry["label"], entry["key"], entry["note"]].compactMap { $0 as? String }.joined(separator: " ").localizedCaseInsensitiveContains(query) }
            return entry["group_id"] as? String == section && (entry["advanced"] as? Bool != true || advanced.state == .on)
        }
        if snapshot.isEmpty { text(t("正在读取设置…", "Loading settings…")) }
        else if filtered.isEmpty && !["models", "support"].contains(section) { text(t("没有匹配的设置。尝试其他关键词或展开高级参数。", "No matching settings. Try another term or show advanced settings.")) }
        for entry in filtered {
            let key = entry["key"] as? String ?? ""
            let row = SettingEditorRow(entry: entry, value: drafts[key] ?? originals[key] ?? raw(entry["value"]), pane: self)
            editors.append(row); add(row)
            let line = NSBox(); line.boxType = .separator; add(line)
        }
        if query.isEmpty, let owner {
            if section == "general" {
                text(t("账户", "Account"), title: true)
                command(t("刷新账号信息", "Refresh account"), code: "identity-refresh=manual")
                command(t("登录或重新登录 BRAIN…", "Sign in to BRAIN…"), code: "brain-login")
            }
            if section == "models" {
                text(t("固定泳道优先于默认模型。路由切换只影响新任务；已开始的工作保留原选择。", "Pinned lanes take priority over defaults. Route changes affect new work; active work retains its selection."))
                menuControl(t("默认研究与审查", "Default research & review"), menu: owner.modelMenu)
                menuControl(t("路由预设", "Routing preset"), menu: owner.presetMenu)
                menuControl(t("渠道开关", "Provider availability"), menu: owner.providerMenu)
                command(t("添加自定义模型…", "Add custom model…"), code: "add-model")
                text(t("额度、超时与重试位于“调用与花费”。", "Budgets, timeouts and retries are under Calls & spending."))
            }
            if section == "scheduling" { menuControl(t("泳道模型分配", "Models per lane"), menu: owner.laneMenu) }
            if section == "support" {
                text("WorldQuant " + (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""), title: true)
                text(t("工作区：", "Workspace: ") + owner.root)
                command(t("设置后台运行…", "Set up background running…"), code: "activate")
                command(t("打开运行日志", "Open run log"), code: "logs")
                command(t("检查更新", "Check for updates"), code: "update")
                command(t("检查 BRAIN 会话", "Check BRAIN session"), code: "brain-check")
                command(t("刷新设置", "Refresh settings"), code: "settings")
                text(t("关闭托盘不等于停止研究。停止研究会等待现有泳道完成；退出时可以选择是否继续后台运行。", "Closing the tray does not stop research. Stop waits for active lanes to finish; quitting lets you choose whether background work continues."))
            }
        }
        updateFooter()
    }
    func changed(_ row: SettingEditorRow) {
        guard !busy else { return }
        if row.input == originals[row.key] { drafts.removeValue(forKey: row.key) } else { drafts[row.key] = row.input }
        updateFooter()
    }
    func updateFooter() {
        save.title = t("保存更改", "Save changes"); revert.title = t("撤销编辑", "Revert edits")
        cancelPending.title = t("取消待应用修改", "Cancel pending changes")
        let operating = snapshot["operating"] as? [String: Any] ?? [:]
        cancelPending.isHidden = operating["pending"] as? Bool != true
        cancelPending.isEnabled = !busy && drafts.isEmpty
        save.isEnabled = !busy && !drafts.isEmpty; revert.isEnabled = !busy && !drafts.isEmpty
        if busy { notice.stringValue = t("正在保存…", "Saving…") }
        else if !drafts.isEmpty { notice.stringValue = t("\(drafts.count) 项尚未保存", "\(drafts.count) unsaved changes") }
        else if operating["pending"] as? Bool == true { notice.stringValue = t("修改已保存，等待现有任务结束后应用。", "Changes saved. Waiting for existing work to finish.") }
        else if let last = operating["last"] as? [String: Any], last["state"] as? String == "failed" { notice.stringValue = last["error"] as? String ?? "" }
        else { notice.stringValue = t("设置已同步", "Settings are up to date") }
        notice.textColor = .secondaryLabelColor
        for row in editors {
            row.field.isEnabled = !busy && row.nullable.state == .off
            for control in [row.choice, row.toggle, row.date, row.nullable] as [NSControl] { control.isEnabled = !busy }
        }
    }
    func result(_ message: String, success: Bool) {
        busy = false
        if success { drafts.removeAll(); originals.removeAll() }
        updateFooter(); notice.stringValue = message; notice.textColor = success ? .secondaryLabelColor : .systemRed
    }
    @objc func selectSection(_ sender: NSButton) { section = sender.identifier?.rawValue ?? "general"; search.stringValue = ""; render() }
    @objc func toggleAdvanced() { render() }
    func controlTextDidChange(_ notification: Notification) { render() }
    @objc func revertChanges() { drafts.removeAll(); originals.removeAll(); receive(snapshot) }
    @objc func cancelSaved() { owner?.perform("operating-cancel") }
    @objc func saveChanges() {
        guard owner?.actionBusy != true else { notice.stringValue = t("请等待当前操作完成。", "Wait for the current action to finish."); return }
        guard !busy, !drafts.isEmpty, let data = try? JSONSerialization.data(withJSONObject: ["values": drafts, "revision": (snapshot["operating"] as? [String: Any])?["revision"] as? String ?? ""]), let value = String(data: data, encoding: .utf8) else { return }
        busy = true; updateFooter(); owner?.perform("settings-save", value)
    }
    @objc func commandClicked(_ sender: NSButton) {
        if !drafts.isEmpty { notice.stringValue = t("请先保存或撤销当前编辑。", "Save or revert your edits first."); return }
        let code = sender.identifier?.rawValue ?? ""
        if code == "add-model" { owner?.addCustomProvider(); return }
        if code == "logs" { owner?.logs(); return }
        if code == "activate" { owner?.activate(); return }
        let parts = code.split(separator: "=", maxSplits: 1).map(String.init)
        owner?.perform(parts[0], parts.count > 1 ? parts[1] : nil)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if drafts.isEmpty { return true }
        let alert = NSAlert(); alert.messageText = t("保留尚未保存的更改？", "Keep your unsaved changes?")
        alert.informativeText = t("关闭窗口会保留编辑，下次打开可继续。", "Closing keeps your edits so you can continue next time.")
        alert.addButton(withTitle: t("关闭并保留", "Close and keep")); alert.addButton(withTitle: t("继续编辑", "Keep editing"))
        return alert.runModal() == .alertFirstButtonReturn
    }
}

/// Research, history and results share one searchable, keyboard-accessible reader.
final class ActivityPane: NSObject, NSTableViewDataSource, NSTableViewDelegate, NSSearchFieldDelegate {
    weak var owner: AppDelegate?
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 700), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
    let segments = NSSegmentedControl()
    let search = NSSearchField()
    let table = NSTableView()
    let detail = NSTextView()
    let status = NSTextField(labelWithString: "")
    let refresh = NSButton(title: "", target: nil, action: nil)
    var selected = "research"
    let kinds = ["research", "history", "submissions", "standby"]
    var responses: [String: [String: Any]] = [:]
    var entries: [[String: Any]] = []
    func t(_ zh: String, _ en: String) -> String { owner?.lang == "en" ? en : zh }
    init(owner: AppDelegate) {
        self.owner = owner; super.init()
        window.isReleasedWhenClosed = false; window.minSize = NSSize(width: 840, height: 560)
        window.setFrameAutosaveName("WorldQuantActivity"); window.title = "WorldQuant"; window.titlebarAppearsTransparent = true
        guard let content = window.contentView else { return }
        segments.segmentCount = 4; segments.trackingMode = .selectOne; segments.selectedSegment = 0
        segments.target = self; segments.action = #selector(selectKind)
        search.delegate = self; search.sendsSearchStringImmediately = true
        refresh.target = self; refresh.action = #selector(reload); refresh.bezelStyle = .rounded
        let toolbar = NSStackView(views: [segments, search, refresh]); toolbar.spacing = 16; toolbar.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(toolbar)
        search.widthAnchor.constraint(greaterThanOrEqualToConstant: 180).isActive = true
        let split = NSSplitView(); split.isVertical = true; split.dividerStyle = .thin; split.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(split)
        let list = NSScrollView(); list.hasVerticalScroller = true; list.autohidesScrollers = true
        let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("record")); column.resizingMask = .autoresizingMask
        table.addTableColumn(column); table.headerView = nil; table.rowHeight = 58; table.usesAlternatingRowBackgroundColors = false
        table.style = .sourceList; table.delegate = self; table.dataSource = self; table.columnAutoresizingStyle = .lastColumnOnlyAutoresizingStyle
        table.setAccessibilityLabel(t("记录列表", "Records")); list.documentView = table
        let reader = NSScrollView(); reader.hasVerticalScroller = true; reader.autohidesScrollers = true
        detail.isEditable = false; detail.isSelectable = true; detail.isRichText = true; detail.drawsBackground = false
        detail.textContainerInset = NSSize(width: 28, height: 24); detail.autoresizingMask = [.width]; detail.isVerticallyResizable = true; detail.isHorizontallyResizable = false
        detail.textContainer?.widthTracksTextView = true; detail.textContainer?.containerSize = NSSize(width: 640, height: CGFloat.greatestFiniteMagnitude)
        reader.documentView = detail; split.addArrangedSubview(list); split.addArrangedSubview(reader)
        status.font = .systemFont(ofSize: 11); status.textColor = .secondaryLabelColor; status.translatesAutoresizingMaskIntoConstraints = false; content.addSubview(status)
        NSLayoutConstraint.activate([
            toolbar.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20), toolbar.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20), toolbar.topAnchor.constraint(equalTo: content.topAnchor, constant: 16),
            split.topAnchor.constraint(equalTo: toolbar.bottomAnchor, constant: 16), split.leadingAnchor.constraint(equalTo: content.leadingAnchor), split.trailingAnchor.constraint(equalTo: content.trailingAnchor), split.bottomAnchor.constraint(equalTo: status.topAnchor, constant: -10),
            status.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 20), status.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -20), status.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -12),
            list.widthAnchor.constraint(greaterThanOrEqualToConstant: 240), reader.widthAnchor.constraint(greaterThanOrEqualToConstant: 480),
        ])
        split.setPosition(300, ofDividerAt: 0); window.center(); render()
    }
    func open(_ kind: String) { selected = kind; search.stringValue = ""; render(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); reload() }
    func receive(_ kind: String, _ response: [String: Any]) { responses[kind] = response; if kind == selected { render() } }
    func render() {
        let labels = [t("研究概览", "Research"), t("轮次历史", "History"), t("已提交", "Submitted"), t("备选", "Standby")]
        for (index, label) in labels.enumerated() { segments.setLabel(label, forSegment: index) }
        segments.selectedSegment = kinds.firstIndex(of: selected) ?? 0
        window.title = "WorldQuant · " + labels[segments.selectedSegment]
        refresh.title = t("刷新", "Refresh"); search.placeholderString = t("搜索记录", "Search records")
        let previous = table.selectedRow >= 0 && table.selectedRow < entries.count ? entries[table.selectedRow]["title"] as? String : nil
        let all = responses[selected]?["entries"] as? [[String: Any]] ?? []
        let query = search.stringValue
        entries = all.filter { row in
            query.isEmpty || ([row["title"] as? String ?? ""] + (row["lines"] as? [String] ?? []) + (row["detail"] as? [String] ?? [])).joined(separator: " ").localizedCaseInsensitiveContains(query)
        }
        table.reloadData()
        let index = entries.firstIndex { $0["title"] as? String == previous } ?? 0
        if !entries.isEmpty { table.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false) } else { showDetail() }
        status.textColor = .secondaryLabelColor
        status.stringValue = responses[selected] == nil ? t("正在读取本地记录…", "Loading local records…") : t("\(entries.count) 条记录 · 数据来自本地账本", "\(entries.count) records · from the local ledger")
    }
    func numberOfRows(in tableView: NSTableView) -> Int { entries.count }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let record = entries[row]
        let cell = NSTableCellView()
        let title = NSTextField(wrappingLabelWithString: record["title"] as? String ?? "")
        title.font = .systemFont(ofSize: 12, weight: .medium); title.maximumNumberOfLines = 2
        title.translatesAutoresizingMaskIntoConstraints = false; cell.addSubview(title); cell.textField = title
        NSLayoutConstraint.activate([title.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 12), title.trailingAnchor.constraint(equalTo: cell.trailingAnchor, constant: -12), title.centerYAnchor.constraint(equalTo: cell.centerYAnchor)])
        if let badge = record["badge"] as? String { title.textColor = UI.badgeColor(badge) ?? .labelColor }
        return cell
    }
    func tableViewSelectionDidChange(_ notification: Notification) { showDetail() }
    func showDetail() {
        let row = table.selectedRow
        guard row >= 0, row < entries.count else { detail.string = t("暂无匹配记录。", "No matching records."); return }
        let entry = entries[row]
        let title = entry["title"] as? String ?? ""
        let lines = entry["detail"] as? [String] ?? entry["lines"] as? [String] ?? []
        let style = NSMutableParagraphStyle(); style.lineSpacing = 5; style.paragraphSpacing = 10
        let result = NSMutableAttributedString(string: title + "\n\n", attributes: [.font: NSFont.systemFont(ofSize: 21, weight: .semibold), .foregroundColor: NSColor.labelColor])
        result.append(NSAttributedString(string: lines.joined(separator: "\n"), attributes: [.font: NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor.labelColor, .paragraphStyle: style]))
        detail.textStorage?.setAttributedString(result); detail.scrollRangeToVisible(NSRange(location: 0, length: 0))
    }
    func error(_ message: String) { status.stringValue = message; status.textColor = .systemRed }
    @objc func selectKind() { selected = kinds[segments.selectedSegment]; search.stringValue = ""; render(); reload() }
    @objc func reload() { status.stringValue = t("正在更新…", "Updating…"); owner?.perform(selected) }
    func controlTextDidChange(_ notification: Notification) { render() }
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
    var laneMenu = NSMenu()
    var timer: Timer?
    var notifyTimer: Timer?
    var actionBusy = false
    var loading = Set<String>()
    /// 同一种读取已经在飞时，只记住一次后续请求。always 盖过 force，force 盖过 stale。完成后只补跑这一次。
    var pendingReadMode: [String: String] = [:]
    var fetched: [String: Date] = [:]
    var wantedHistoryToken = ""
    var displayedHistoryToken = ""
    var historyRequestToken = ""
    var wantedResearchToken = ""
    var displayedResearchToken = ""
    var researchRequestToken = ""
    var researchUpdating = false
    var presetClosed: [String: String] = [:]
    var lockFD: Int32 = -1
    var ready = false
    var root: String { UserDefaults.standard.string(forKey: "WQWorkspace") ?? "" }
    var python: String { UserDefaults.standard.string(forKey: "WQPython") ?? "" }
    var engineSource: String? {
        ProcessInfo.processInfo.environment["WQ_ENGINE_SOURCE"] ?? UserDefaults.standard.string(forKey: "WQEngineSource")
    }
    var configured: Bool { !root.isEmpty && !python.isEmpty }
    let activateRow = NSMenuItem(title: "", action: #selector(activate), keyEquivalent: "")
    let activateSep = NSMenuItem.separator()
    let headline = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let detail = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let cycles = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let submissions = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let standby = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    var standbyMenu = NSMenu()
    let research = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    var researchMenu = NSMenu()
    var settingsPane: SettingsPane?
    var activityPane: ActivityPane?
    var activityResponses: [String: [String: Any]] = [:]
    var researchResponse: [String: Any] = [:]

    let tick = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let nextAt = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let experimentRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let researchModel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let reviewModel = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let lanesRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let accountRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let brainRow = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let cycleRow = NSMenuItem(title: "", action: #selector(cycleAction), keyEquivalent: "")
    let powerRow = NSMenuItem(title: "", action: #selector(powerAction), keyEquivalent: "")
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
    /// 单行/多行信息行：自定义视图 + 折行标签，宽度统一 UI.menuWidth，长文本折行不截断。
    /// 内容未变化时保留原视图，避免 15 秒状态刷新反复重建。
    func wrapRow(_ row: NSMenuItem, _ value: String, font: NSFont = UI.fontMeta, color: NSColor = .secondaryLabelColor) {
        let attr = NSAttributedString(string: value, attributes: [.font: font, .foregroundColor: color])
        row.title = value
        row.toolTip = value
        row.isEnabled = false
        if let field = row.view?.subviews.compactMap({ $0 as? NSTextField }).first,
           field.attributedStringValue.isEqual(to: attr) { return }
        let field = NSTextField(wrappingLabelWithString: "")
        field.attributedStringValue = attr
        let textWidth = UI.menuWidth - UI.menuPadX * 2
        field.preferredMaxLayoutWidth = textWidth
        let height = ceil(field.cell!.cellSize(forBounds: NSRect(x: 0, y: 0, width: textWidth, height: 10000)).height)
        field.frame = NSRect(x: UI.menuPadX, y: UI.menuRowPadY, width: textWidth, height: height)
        let view = NSView(frame: NSRect(x: 0, y: 0, width: UI.menuWidth, height: height + UI.menuRowPadY * 2))
        view.addSubview(field)
        view.setAccessibilityElement(true)
        view.setAccessibilityLabel(value)
        row.view = view
    }
    func info(_ text: String) -> NSMenuItem {
        let row = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        wrapRow(row, text)
        return row
    }
    /// 卡片式菜单行：默认首行作标题（13 semibold，可按徽章着色）+ 其余作正文（12 辅助色）；
    /// heading=false 时整组按正文渲染（用于没有天然标题的详情面板）。
    func block(_ lines: [String], width: CGFloat = UI.menuWidth, titleColor: NSColor? = nil, heading: Bool = true) -> NSMenuItem {
        let text = NSTextField(wrappingLabelWithString: "")
        let attr = NSMutableAttributedString()
        let rest = heading ? lines.dropFirst() : []
        if heading, let title = lines.first {
            let style = NSMutableParagraphStyle()
            style.paragraphSpacing = rest.isEmpty ? 0 : UI.titleBodyGap
            attr.append(NSAttributedString(string: title + (rest.isEmpty ? "" : "\n"), attributes: [
                .font: UI.fontTitle,
                .foregroundColor: titleColor ?? NSColor.labelColor,
                .paragraphStyle: style]))
        }
        let body = heading ? Array(rest) : lines
        if !body.isEmpty {
            let style = NSMutableParagraphStyle()
            style.lineSpacing = 2
            attr.append(NSAttributedString(string: body.joined(separator: "\n"), attributes: [
                .font: UI.fontMeta,
                .foregroundColor: NSColor.secondaryLabelColor,
                .paragraphStyle: style]))
        }
        text.attributedStringValue = attr
        let textWidth = width - UI.menuPadX * 2
        text.preferredMaxLayoutWidth = textWidth
        let height = ceil(text.cell!.cellSize(forBounds: NSRect(x: 0, y: 0, width: textWidth, height: 10000)).height)
        text.frame = NSRect(x: UI.menuPadX, y: UI.menuBlockPadY, width: textWidth, height: height)
        let view = NSView(frame: NSRect(x: 0, y: 0, width: width, height: height + UI.menuBlockPadY * 2))
        view.addSubview(text)
        view.setAccessibilityElement(true)
        view.setAccessibilityLabel(lines.joined(separator: "，"))
        let row = NSMenuItem(title: lines.first ?? "", action: nil, keyEquivalent: "")
        row.view = view; row.isEnabled = false
        return row
    }
    /// 对话框会激活这个菜单栏应用，AppKit 随后投递 applicationShouldHandleReopen。
    /// 那条路径本来只该在用户再次打开应用时显示研究进展；对话框期间先压住，并多留一个主线程回合，
    /// 盖住模态结束后才送到的同一次事件。
    var researchReopenHolds = 0
    func holdResearchReopen<T>(_ body: () -> T) -> T {
        researchReopenHolds += 1
        let value = body()
        DispatchQueue.main.async { [weak self] in
            DispatchQueue.main.async {
                guard let self else { return }
                self.researchReopenHolds = max(0, self.researchReopenHolds - 1)
            }
        }
        return value
    }
    @discardableResult
    func runAlert(_ box: NSAlert) -> NSApplication.ModalResponse {
        holdResearchReopen {
            NSApp.activate(ignoringOtherApps: true)
            return box.runModal()
        }
    }
    func alert(_ title: String, _ text: String) {
        let box = NSAlert(); box.messageText = title; box.informativeText = text; runAlert(box)
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
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if ready && researchReopenHolds == 0 { showResearch() }
        return true
    }
    /// 研究进展窗口：工具行（标题左 + 按钮右）+ 分隔线 + 卡片式分节列表，全部 Auto Layout。
    @objc func showResearch() {
        if activityPane == nil { activityPane = ActivityPane(owner: self) }
        for (kind, response) in activityResponses { activityPane?.receive(kind, response) }
        activityPane?.open("research")
    }
    @objc func showActivity(_ sender: NSMenuItem) {
        if activityPane == nil { activityPane = ActivityPane(owner: self) }
        for (kind, response) in activityResponses { activityPane?.receive(kind, response) }
        activityPane?.open(sender.representedObject as? String ?? "research")
    }
    func updateResearchWindow() { activityPane?.receive("research", researchResponse) }
    @objc func refreshResearch() { refreshResearchIfNeeded(always: true) }
    @objc func openTrayMenu() { item.button?.performClick(nil) }
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
        switch runAlert(box) {
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
        guard holdResearchReopen({ panel.runModal() }) == .OK, let url = panel.url else { return nil }
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
            if let source = self.engineSource, !source.isEmpty {
                var environment = ProcessInfo.processInfo.environment
                environment["WQ_ENGINE_SOURCE"] = source
                process.environment = environment
            }
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
        perform("history"); perform("submissions"); perform("standby"); perform("research"); perform("notifications"); perform("settings")
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        if UserDefaults.standard.string(forKey: "WQResearchWindowVersion") != version {
            UserDefaults.standard.set(version, forKey: "WQResearchWindowVersion")
            showResearch()
        }
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
                    lanesRow, cycles, submissions, standby, research,
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
        // 状态区：标题 13 semibold + 说明/调度/模型 12 辅助色，全部折行，等状态回填。
        wrapRow(headline, t("正在读取状态…", "Reading status…"), font: UI.fontTitle, color: .labelColor)
        for row in [detail, tick, nextAt, experimentRow, researchModel, reviewModel, lanesRow] { wrapRow(row, " ") }
        for row in [headline, detail, nextAt] { menu.addItem(row) }
        let runDetails = NSMenu(); runDetails.autoenablesItems = false
        for row in [tick, experimentRow, researchModel, reviewModel, lanesRow] { runDetails.addItem(row) }
        let detailsRow = NSMenuItem(title: t("运行详情", "Run details"), action: nil, keyEquivalent: "")
        detailsRow.submenu = runDetails; menu.addItem(detailsRow)
        powerRow.target = self; cycleRow.target = self
        applyRunState(paused: true, enabled: false, cycleOpen: false)
        for row in [powerRow, cycleRow] { menu.addItem(row); controls.append(row) }

        menu.addItem(.separator())
        historyMenu = NSMenu(title: t("轮次历史", "Cycle history"))
        submissionsMenu = NSMenu(title: t("已提交 Alpha", "Submitted Alphas"))
        standbyMenu = NSMenu(title: t("备选 Alpha", "Standby Alphas"))
        researchMenu = NSMenu(title: t("研究进展", "Research progress"))
        research.title = t("研究概览…", "Research overview…")
        cycles.title = t("轮次历史…", "Cycle history…")
        submissions.title = t("已提交 Alpha…", "Submitted Alphas…")
        standby.title = t("备选 Alpha…", "Standby Alphas…")
        for (row, submenu) in [(research, researchMenu), (cycles, historyMenu), (submissions, submissionsMenu), (standby, standbyMenu)] {
            submenu.autoenablesItems = false; submenu.delegate = self
            submenu.addItem(info(t("正在读取本地账本…", "Reading the local ledger…")))
            row.submenu = nil; row.action = #selector(showActivity(_:)); row.target = self
            row.representedObject = row === research ? "research" : (row === cycles ? "history" : (row === submissions ? "submissions" : "standby"))
            row.isEnabled = true; menu.addItem(row)
        }
        menu.addItem(.separator())
        settingsMenu = NSMenu(title: t("设置", "Settings"))
        for submenu in [settingsMenu, presetMenu, providerMenu, modelMenu, laneMenu] { submenu.removeAllItems(); submenu.autoenablesItems = false }
        let settingsRow = NSMenuItem(title: t("设置…", "Settings…"), action: nil, keyEquivalent: "")
        settingsRow.action = #selector(showSettings); settingsRow.target = self; settingsRow.keyEquivalent = ","; settingsRow.isEnabled = true
        menu.addItem(settingsRow)
        menu.addItem(.separator())
        cycleRow.target = self
        powerRow.target = self
        applyRunState(paused: true, enabled: false, cycleOpen: false)

        for (title, selector, tip) in [
            (t("退出…", "Quit…"), #selector(quit),
             t("选择只关菜单、本轮结束后停止，或立刻结束本地任务。研究调度不依赖菜单是否开着。",
               "Choose to close the menu only, stop after this cycle, or stop local tasks now. The scheduler does not need this menu to stay open."))
        ] {
            let row = NSMenuItem(title: title, action: selector, keyEquivalent: "")
            row.target = self; row.toolTip = tip; menu.addItem(row); controls.append(row)
        }
        updateActivateRow()
    }
    func menuWillOpen(_ opened: NSMenu) {
        guard ready else { return }
        if opened === historyMenu { refreshHistoryIfNeeded(force: true) }
        else if opened === submissionsMenu { load("submissions") }
        else if opened === standbyMenu { load("standby") }
        else if opened === researchMenu { refreshResearchIfNeeded(force: true) }
        else if opened === settingsMenu { updateActivateRow(); load("settings") }
        else if opened === menu { refresh() }
    }
    func menu(for kind: String) -> NSMenu? {
        switch kind {
        case "history": return historyMenu
        case "submissions": return submissionsMenu
        case "standby": return standbyMenu
        case "research": return researchMenu
        case "settings": return settingsMenu
        default: return nil
        }
    }
    func showListLoading(_ target: NSMenu) {
        if target.items.first?.tag == 1 { return }
        let placeholder = target.items.allSatisfy {
            $0.isSeparatorItem || $0.tag == 1 || $0.title.hasPrefix(t("正在读取", "Reading")) || $0.title.hasPrefix(t("正在更新", "Updating"))
        }
        if placeholder { return }
        let row = info(t("正在更新…", "Updating…"))
        row.tag = 1
        target.insertItem(row, at: 0)
    }
    func load(_ kind: String) {
        let stale = fetched[kind] == nil || Date().timeIntervalSince(fetched[kind]!) > 30
        if !stale { return }
        if let target = menu(for: kind) { showListLoading(target) }
        perform(kind)
    }
    func showHistoryLoading() {
        showListLoading(historyMenu)
    }
    func refreshHistoryIfNeeded(force: Bool = false) {
        let stale = wantedHistoryToken != displayedHistoryToken
        if !force && !stale { return }
        if force && !stale && !loading.contains("history") { return }
        if !displayedHistoryToken.isEmpty { showHistoryLoading() }
        if loading.contains("history") {
            notePendingRead("history", force ? "force" : "stale")
            return
        }
        historyRequestToken = wantedHistoryToken
        perform("history")
    }
    func refreshResearchIfNeeded(force: Bool = false, always: Bool = false) {
        let stale = wantedResearchToken != displayedResearchToken
        if !always && !force && !stale { return }
        if !always && force && !stale && !loading.contains("research") { return }
        showListLoading(researchMenu)
        if !researchResponse.isEmpty { researchUpdating = true; updateResearchWindow() }
        if loading.contains("research") {
            notePendingRead("research", always ? "always" : (force ? "force" : "stale"))
            return
        }
        researchRequestToken = wantedResearchToken
        perform("research")
    }
    func notePendingRead(_ action: String, _ mode: String) {
        let rank = ["stale": 1, "force": 2, "always": 3]
        let previous = pendingReadMode[action] ?? ""
        if (rank[mode] ?? 0) >= (rank[previous] ?? 0) {
            pendingReadMode[action] = mode
        }
    }
    func settleRead(_ action: String) {
        guard let mode = pendingReadMode.removeValue(forKey: action) else { return }
        switch action {
        case "history":
            refreshHistoryIfNeeded(force: mode == "force" || mode == "always")
        case "research":
            refreshResearchIfNeeded(force: mode == "force", always: mode == "always")
        case "submissions", "standby", "settings":
            fetched[action] = nil
            load(action)
        case "notifications":
            perform("notifications")
        case "status":
            refresh()
        case "identity-refresh":
            perform("identity-refresh")
        default:
            break
        }
    }
    func applyIcon(active: Bool) {
        // 模板图标本身是单色。暂停时再降低不透明度，和旁边常亮的菜单栏图标区分开。
        item.button?.alphaValue = active ? 1 : 0.4
    }
    func applyRunState(paused: Bool, enabled: Bool, cycleOpen: Bool, lanes: [[String: Any]] = []) {
        let running = !paused && enabled
        powerStops = running
        powerRow.title = running ? t("停止自动研究", "Stop automatic research") : t("开始自动研究", "Start automatic research")
        powerRow.toolTip = running
            ? t("不再创建新轮次；所有在途泳道允许收尾。要立刻结束这一轮，用「取消当前轮次」。",
               "Stops creating new cycles; all active lanes may finish. Use “Cancel current cycle” to end that cycle now.")
            : t("恢复队列，按既有间隔持续研究。", "Resumes the queue and keeps researching at the configured interval.")
        cycleRow.submenu = nil
        cycleCancels = cycleOpen
        let openLanes = lanes.filter { $0["cycle_id"] as? Int != nil }
        if openLanes.count > 1 {
            cycleRow.isHidden = false
            cycleRow.action = nil
            cycleRow.title = t("取消轮次…", "Cancel cycle…")
            cycleRow.toolTip = t("结束某一条泳道的这一轮；其它泳道不受影响，自动研究保持开启。已经发出的模拟不会撤回。",
                                 "Ends one lane’s cycle; other lanes are unaffected and automatic research stays on. A simulation already sent is not withdrawn.")
            let picker = NSMenu(title: cycleRow.title)
            picker.autoenablesItems = false
            for item in openLanes {
                let lane = (item["lane"] as? Int ?? 0) + 1
                let cid = item["cycle_id"] as? Int ?? 0
                let state = item["state_label"] as? String ?? item["state"] as? String ?? ""
                let row = NSMenuItem(title: t("泳道\(lane) · 第\(cid)轮 · \(state)", "Lane \(lane) · cycle \(cid) · \(state)"),
                                     action: #selector(apply(_:)), keyEquivalent: "")
                row.target = self
                row.representedObject = "cancel-cycle=\(cid)"
                picker.addItem(row)
            }
            cycleRow.submenu = picker
            cycleRow.isEnabled = true
            return
        }
        cycleRow.action = #selector(cycleAction)
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
        switch runAlert(box) {
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
        if settingsPane?.drafts.isEmpty == false { settingsPane?.result(t("请先保存或撤销表单中的更改。", "Save or revert your form edits first."), success: false); return }
        guard let code = sender.representedObject as? String else { return }
        let parts = code.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        if parts[0] == "preset", parts.count > 1 {
            if let blocked = presetClosed[parts[1]], !blocked.isEmpty {
                let box = NSAlert()
                box.messageText = t("还不能切换这个预设", "This preset cannot be set yet")
                box.informativeText = t("请先在「渠道」里打开：", "Turn these providers on under Providers first: ") + blocked
                    + t("。打开后才能设置。", ". You can set the preset after they are on.")
                box.addButton(withTitle: t("好", "OK"))
                _ = runAlert(box)
                return
            }
            // 切换预设前让用户选择：临时 N 轮（步进器可调，默认 1）还是永久切换。
            let box = NSAlert()
            box.messageText = t("切换路由预设：", "Switch routing preset: ") + parts[1]
            box.informativeText = t("临时切换：接下来 N 个新建研究轮次使用该预设（N 用上方步进器调整），用完自动恢复当前永久预设。\n永久切换：之后所有新任务都使用该预设。\n在途任务都保持原路由。",
                                    "Temporary: the next N new research cycles use this preset (set N with the stepper above), then the current permanent preset resumes.\nPermanent: all new tasks use it from now on.\nIn-flight tasks keep their routes either way.")
            let cyclesInput = PresetCyclesInput(alert: box, lang: lang)
            box.accessoryView = cyclesInput.row
            cyclesInput.onceButton = box.addButton(withTitle: cyclesInput.buttonTitle())
            box.addButton(withTitle: t("永久切换", "Permanent"))
            box.addButton(withTitle: t("取消", "Cancel"))
            switch runAlert(box) {
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
        activityResponses[kind] = response
        activityPane?.receive(kind, response)
        if kind == "research" { researchUpdating = false; researchResponse = response }
        if kind == "history" { displayedHistoryToken = historyRequestToken }
        if kind == "research" { displayedResearchToken = researchRequestToken }
        let count = (response["entries"] as? [[String: Any]] ?? []).count
        if kind == "submissions" { submissions.title = t("已提交 Alpha（\(count)）…", "Submitted Alphas (\(count))…") }
        if kind == "standby" { standby.title = t("备选 Alpha（\(count)）…", "Standby Alphas (\(count))…") }
        fetched[kind] = Date()
    }
    func fillSettings(_ response: [String: Any]) {
        let responseLang = response["language"] as? String ?? "zh"
        // 先按新语言重建，再重拉所有列表数据（不靠 fetched 判定：launch 等非只读动作会清空 fetched，
        // 竞态下会漏掉已回填的子菜单标题，滞留旧语言）。
        if responseLang != lang {
            lang = responseLang
            buildMainMenu()
            updateResearchWindow()
            fetched.removeAll()
            for kind in ["history", "submissions", "standby", "research"] { perform(kind) }
        }
        presetMenu.removeAllItems()
        presetClosed = [:]
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
            let closed = preset["closed"] as? [String] ?? []
            presetClosed[name] = closed.joined(separator: lang == "zh" ? "、" : ", ")
            let row = NSMenuItem(title: name, action: #selector(apply(_:)), keyEquivalent: "")
            row.target = self
            row.representedObject = "preset=" + name
            row.state = name == active ? .on : .off
            row.toolTip = preset["routes"] as? String ?? ""
            if !research.isEmpty || !review.isEmpty {
                let style = NSMutableParagraphStyle()
                style.paragraphSpacing = 2
                let caption = NSMutableAttributedString(string: name + "\n", attributes: [
                    .font: UI.fontTitle,
                    .foregroundColor: NSColor.labelColor,
                    .paragraphStyle: style])
                caption.append(NSAttributedString(
                    string: t("研究  ", "Research  ") + research + "\n" + t("审查  ", "Review  ") + review,
                    attributes: [
                        .font: UI.fontCaption,
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
        modelMenu.addItem(info(t("编辑永久预设：", "Editing permanent preset: ") + (response["editable_preset"] as? String ?? "") + t("。在途工作完成后应用；固定泳道优先。", ". Applies after active work; lane pins take priority.")))
        let researchHead = response["editable_research_provider"] as? String ?? response["research_provider"] as? String ?? ""
        let reviewHead = response["editable_review_provider"] as? String ?? response["review_provider"] as? String ?? ""
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
        laneMenu.removeAllItems()
        laneMenu.addItem(info(t("每条泳道是一轮完整的研究→审查→模拟；对在途轮次不打断，下一轮起生效。",
                                "Each lane is a full research→review→simulate cycle; in-flight cycles are not interrupted.")))
        let currentLanes = response["concurrent_lanes"] as? Int ?? 1
        let pairOptions = response["lane_pair_options"] as? [[String: Any]] ?? []
        let pins = response["lane_pins"] as? [String: [String: String]] ?? [:]
        if currentLanes >= 1 && !pairOptions.isEmpty {
            laneMenu.addItem(.separator())
            laneMenu.addItem(info(t("泳道固定：某条泳道每轮都用选定的研究→审查对；渠道不可用时该泳道等待，不自动换对。",
                                    "Lane pinning: a pinned lane always uses that research→review pair; when a provider is unavailable the lane waits instead of substituting.")))
            for laneNo in 0..<currentLanes {
                let picker = NSMenu(title: t("泳道\(laneNo + 1)", "Lane \(laneNo + 1)"))
                let pin = pins[String(laneNo)]
                pickRow(t("自动错开", "Auto-stagger"), "config=lane_pin=\(laneNo + 1):off", on: pin == nil,
                        tip: t("按预设路由与其它泳道自动错开。", "Picks automatically, staggered against the other lanes."),
                        in: picker)
                for opt in pairOptions {
                    let research = opt["research"] as? String ?? ""
                    let review = opt["review"] as? String ?? ""
                    pickRow(opt["label"] as? String ?? "\(research) → \(review)",
                            "config=lane_pin=\(laneNo + 1):\(research):\(review)",
                            on: pin?["research"] == research && pin?["review"] == review,
                            tip: t("下一次该泳道建轮生效。", "Applies to the lane's next cycle."),
                            in: picker)
                }
                let row = NSMenuItem(title: t("泳道\(laneNo + 1)", "Lane \(laneNo + 1)"), action: nil, keyEquivalent: "")
                row.submenu = picker
                laneMenu.addItem(row)
            }
        }
        settingsPane?.receive(response)
        fetched["settings"] = Date()
    }

    @objc func showSettings() {
        if settingsPane == nil { settingsPane = SettingsPane(owner: self) }
        settingsPane?.open(); perform("settings")
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
        guard runAlert(box) == .alertFirstButtonReturn else { return }
        guard let spec = form.spec() else {
            alert(t("还不能添加", "Cannot add it yet"), t("名称、协议、地址和模型 ID 都要填写。", "Name, protocol, address and model ID are all required."))
            return
        }
        var env: [String: String] = [:]
        if let key = form.key(), !key.isEmpty { env["WQ_PROVIDER_KEY"] = key }
        perform("provider-add", spec, env: env)
    }
    func perform(_ action: String, _ arg: String? = nil, env: [String: String] = [:]) {
        let readOnly = ["status", "history", "submissions", "standby", "research", "settings", "notifications", "identity-refresh"].contains(action)
        if loading.contains(action) || (!readOnly && actionBusy) {
            if readOnly && loading.contains(action) { notePendingRead(action, "stale") }
            return
        }
        if action == "history" { historyRequestToken = wantedHistoryToken }
        if action == "research" { researchRequestToken = wantedResearchToken }
        loading.insert(action)
        if !readOnly {
            actionBusy = true; controls.forEach { $0.isEnabled = false }; wrapRow(headline, t("正在处理…", "Working…"), font: UI.fontTitle, color: .labelColor)
        }
        DispatchQueue.global(qos: .utility).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.python)
            process.arguments = ["-m", "wq.desktop_control", action] + (arg.map { [$0] } ?? [])
            process.currentDirectoryURL = URL(fileURLWithPath: self.root)
            var environment = ProcessInfo.processInfo.environment
            for (key, value) in env { environment[key] = value }
            if let source = self.engineSource, !source.isEmpty {
                environment["WQ_ENGINE_SOURCE"] = source
                environment["PYTHONPATH"] = source
            } else if FileManager.default.fileExists(atPath: self.root + "/src/wq/__init__.py") {
                let src = self.root + "/src"
                environment["PYTHONPATH"] = environment["PYTHONPATH"].map { src + ":" + $0 } ?? src
            }
            process.environment = environment
            let pipe = Pipe(); process.standardOutput = pipe; process.standardError = pipe
            var result: [String: Any] = [:]; var success = false
            do {
                if let source = self.engineSource, !source.isEmpty,
                   !FileManager.default.fileExists(atPath: source + "/wq/__init__.py") {
                    throw NSError(domain: "WorldQuant", code: 1, userInfo: [NSLocalizedDescriptionKey:
                        self.t("固定引擎目录不可用，请恢复本地部署", "Pinned engine directory unavailable; restore the local deployment")])
                }
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
                    if action == "research" {
                        self.researchUpdating = false
                        self.updateResearchWindow()
                    }
                    if action == "identity-refresh" {
                        self.settleRead(action)
                        return
                    }
                    if action == "history" || action == "submissions" || action == "standby" || action == "research" {
                        self.activityPane?.error(error)
                        let target = action == "research" ? self.researchMenu : (action == "history" ? self.historyMenu : (action == "standby" ? self.standbyMenu : self.submissionsMenu))
                        target.removeAllItems(); target.addItem(self.block([error]))
                        self.settleRead(action)
                        return
                    }
                    if action == "settings-save" || action == "settings" || action == "operating-cancel" { self.settingsPane?.result(error, success: false) }
                    self.fetched["settings"] = nil
                    self.wrapRow(self.headline, self.t("读取失败", "Read failed"), font: UI.fontTitle, color: .labelColor)
                    self.wrapRow(self.detail, error)
                    self.item.button?.toolTip = error
                    if action == "launch" {
                        self.refresh()
                    } else if !readOnly && action != "settings-save" {
                        let alert = NSAlert(); alert.messageText = self.t("操作未完成", "Action failed"); alert.informativeText = error; self.runAlert(alert)
                    }
                    self.settleRead(action)
                } else if action == "quit-after-cycle" || action == "quit-now" { NSApp.terminate(nil)
                } else if action == "history" || action == "submissions" || action == "standby" || action == "research" {
                    self.fill(action, response)
                    self.settleRead(action)
                } else if action == "update" {
                    let message = response["message"] as? String ?? self.t("检查更新失败", "Update check failed")
                    self.alert("WorldQuant", message)
                    if response["update"] as? Bool == true, let raw = response["url"] as? String, let link = URL(string: raw) {
                        NSWorkspace.shared.open(link)
                    }
                } else if action == "settings" {
                    self.fillSettings(response)
                    self.settleRead(action)
                } else if action == "notifications" {
                    self.postNotifications(response)
                    self.settleRead(action)
                } else if action == "identity-refresh" {
                    self.applyIdentity(response["identity"] as? [String: Any])
                    if let message = response["message"] as? String, !message.isEmpty {
                        self.alert("WorldQuant", message)
                    }
                    self.settleRead(action)
                } else if action == "status" {
                    if self.actionBusy {
                        self.settleRead(action)
                        return
                    }
                    let identity = response["identity"] as? [String: Any] ?? [:]
                    self.applyIdentity(identity)
                    self.wrapRow(self.headline, response["title"] as? String ?? self.t("状态未知", "Status unknown"),
                                 font: UI.fontTitle, color: .labelColor)
                    self.wrapRow(self.detail, response["message"] as? String ?? " ")
                    self.cycles.title = self.t("轮次历史", "Cycle history")
                    self.cycles.toolTip = response["cycles"] as? String ?? ""
                    self.wrapRow(self.tick, self.t("最近调度：", "Last tick: ") + (response["last_tick"] as? String ?? self.t("尚无记录", "No records yet")))
                    self.wrapRow(self.nextAt, self.t("下一轮：", "Next cycle: ") + (response["next_at"] as? String ?? self.t("待当前任务完成／调度检查", "awaiting current task / scheduler check")))
                    self.wrapRow(self.experimentRow, response["experiment_line"] as? String ?? self.t("实验轮数：未设置", "Experiment cycles: not set"))
                    self.wantedHistoryToken = response["history_token"] as? String ?? ""
                    self.wantedResearchToken = response["research_token"] as? String ?? ""
                    self.refreshHistoryIfNeeded()
                    self.refreshResearchIfNeeded()
                    if (identity["detail"] as? String ?? "").isEmpty {
                        self.textRow(self.brainRow, (response["brain_bound"] as? Bool) == true
                                     ? self.t("已登录", "Signed in")
                                     : self.t("未登录；用「绑定 / 重新登录」", "Not signed in; use “Bind / re-login”"))
                    }
                    let models = response["next_models"] as? [[String: Any]] ?? []
                    for (index, row) in [self.researchModel, self.reviewModel].enumerated() {
                        self.wrapRow(row, self.t("下轮", "Next: ") + (index < models.count ? models[index]["title"] as? String ?? self.t("未知", "unknown") : self.t("未知", "unknown")))
                        row.toolTip = self.t("预估下一轮路由；任务领取时冻结，重试可能切换备用渠道。",
                                             "Estimated routing for the next cycle; frozen at claim time, retries may switch to a fallback provider.")
                    }
                    let lanes = response["lanes"] as? [[String: Any]] ?? []
                    if lanes.isEmpty {
                        self.lanesRow.isHidden = true
                    } else {
                        self.lanesRow.isHidden = false
                        var lines: [String] = []
                        for item in lanes {
                            let lane = (item["lane"] as? Int ?? 0) + 1
                            let state = item["state_label"] as? String ?? item["state"] as? String ?? ""
                            var line = "Lane \(lane)"
                            if let cid = item["cycle_id"] as? Int {
                                line = self.t("泳道\(lane) · 第\(cid)轮 · \(state)", "Lane \(lane) · cycle \(cid) · \(state)")
                            } else {
                                line = self.t("泳道\(lane) · \(state)", "Lane \(lane) · \(state)")
                            }
                            var pair = [item["research"] as? String, item["review"] as? String].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " → ")
                            if item["pinned"] as? Bool == true && !pair.isEmpty { pair += self.t(" · 固定", " · pinned") }
                            if !pair.isEmpty { line += self.t("（\(pair)）", " (\(pair))") }
                            lines.append(line)
                        }
                        self.wrapRow(self.lanesRow, lines.joined(separator: "\n"))
                        self.lanesRow.toolTip = self.t("每条泳道是一轮完整的研究→审查→模拟；模拟与提交仍串行。",
                                                       "Each lane is a full research→review→simulate cycle; simulation and submission stay serial.")
                    }
                    let paused = response["paused"] as? Bool == true
                    let enabled = response["enabled"] as? Bool == true
                    let cycleOpen = response["cycle_open"] as? Bool == true
                    self.applyRunState(paused: paused, enabled: enabled, cycleOpen: cycleOpen, lanes: lanes)
                    if response["stop_after_cycle"] as? Bool == true {
                        self.powerRow.title = self.t("正在收尾…", "Finishing active lanes…")
                        self.powerRow.isEnabled = false
                    } else { self.powerRow.isEnabled = !self.actionBusy }
                    self.applyIcon(active: !paused && enabled)
                    self.item.button?.toolTip = self.headline.title + "\n" + self.detail.title
                    self.settleRead(action)
                } else if action.hasPrefix("brain-") {
                    if let message = response["message"] as? String, !message.isEmpty {
                        self.alert("WorldQuant", message)
                    }
                    self.refresh()
                } else {
                    if action == "settings-save" || action == "operating-cancel" { self.settingsPane?.result(response["message"] as? String ?? "", success: true) }
                    let quiet = ["settings-save", "operating-cancel", "start", "pause", "run-next", "cancel-cycle", "config", "launch",
                                 "quit-after-cycle", "quit-now", "provider-add", "provider-role",
                                 "preset", "preset-once", "preset-cancel", "provider"].contains(action)
                    if let message = response["message"] as? String, !message.isEmpty {
                        self.wrapRow(self.detail, message)
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
