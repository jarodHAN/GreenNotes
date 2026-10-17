// LocalNotes.app —— macOS 原生外壳
// 拉起本地 Python 服务 → 等端口就绪 → WKWebView 加载页面 → 退出时回收服务
// 仅用系统自带的 swiftc + WebKit，无 Electron / py2app / Rust。
import Cocoa
import WebKit
import Foundation

// ---------- 日志（GUI 看不到 stdout，全部落盘） ----------
let logDir = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Logs/LocalNotes")
try? FileManager.default.createDirectory(atPath: logDir, withIntermediateDirectories: true)
let logPath = (logDir as NSString).appendingPathComponent("app.log")
func logLine(_ s: String) {
    let line = "\(Date())  \(s)\n"
    guard let data = line.data(using: .utf8) else { return }
    if let fh = FileHandle(forWritingAtPath: logPath) {
        fh.seekToEndOfFile()
        fh.write(data)
        try? fh.close()
    } else {
        try? data.write(to: URL(fileURLWithPath: logPath))
    }
}

NSSetUncaughtExceptionHandler { ex in
    logLine("❌ 未捕获异常 \(ex.name.rawValue): \(ex.reason ?? "")")
    logLine("   调用栈: \((ex.callStackSymbols.prefix(6)).joined(separator: " | "))")
}

// ---------- 路径 ----------
let exeDir = URL(fileURLWithPath: CommandLine.arguments[0])
    .deletingLastPathComponent()          // .../MacOS
    .deletingLastPathComponent()         // .../Contents
    .appendingPathComponent("Resources")
let appDir = exeDir.appendingPathComponent("app")                 // server.py + static + importers
let dataDir = (NSHomeDirectory() as NSString)
    .appendingPathComponent("Library/Application Support/LocalNotes")
try? FileManager.default.createDirectory(atPath: dataDir, withIntermediateDirectories: true)

func findPython() -> String? {
    let fm = FileManager.default
    var cands: [String] = []
    if let e = ProcessInfo.processInfo.environment["APP_PYTHON"], !e.isEmpty { cands.append(e) }
    cands.append(exeDir.appendingPathComponent("python/bin/python3").path)   // 包内自带的解释器
    cands.append("/usr/bin/python3")
    cands.append("/usr/local/bin/python3")
    cands.append("/opt/homebrew/bin/python3")
    for c in cands where fm.isExecutableFile(atPath: c) { return c }
    return nil
}

// 挑一个空闲端口（bind 0 让内核分配）
func freePort() -> Int {
    var addr = sockaddr_in()
    addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = 0
    addr.sin_addr.s_addr = INADDR_ANY
    let fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP)
    guard fd >= 0 else { return 8731 }
    var a = addr
    withUnsafePointer(to: &a) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { ptr in
            _ = bind(fd, ptr, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    var got = sockaddr_in()
    var glen = socklen_t(MemoryLayout<sockaddr_in>.size)
    withUnsafeMutablePointer(to: &got) { p in
        p.withMemoryRebound(to: sockaddr.self, capacity: 1) { ptr in
            _ = getsockname(fd, ptr, &glen)
        }
    }
    close(fd)
    return Int(CFSwapInt16BigToHost(got.sin_port))
}

func waitForServer(port: Int, timeout: Double = 25) -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if let u = URL(string: "http://127.0.0.1:\(port)/api/state"),
           let d = try? Data(contentsOf: u), !d.isEmpty,
           let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
           (j["ok"] as? Bool) == true { return true }
        Thread.sleep(forTimeInterval: 0.4)
    }
    return false
}

// ---------- 启动服务 ----------
let port = freePort()
logLine("资源目录: \(exeDir.path)")
logLine("数据目录: \(dataDir)")
guard let py = findPython() else {
    logLine("未找到 Python 解释器")
    fatalError("no python")
}
let serverLog = (logDir as NSString).appendingPathComponent("server.log")
let proc = Process()
proc.executableURL = URL(fileURLWithPath: py)
// -B：不要写 .pyc。App 包里的 Resources/app 是可写的，Python 默认会把 __pycache__
// 写进去，这会破坏 code signature 的封条，第二次启动就可能被判「已损坏」。
proc.arguments = ["-B", appDir.appendingPathComponent("server.py").path, "--port", String(port), "--parent", String(getpid())]
proc.currentDirectoryURL = appDir
var env = ProcessInfo.processInfo.environment
env["LOCALNOTES_DATA"] = dataDir
env["LOCALNOTES_PORT"] = String(port)
env["PYTHONDONTWRITEBYTECODE"] = "1"
proc.environment = env
if let fh = FileHandle(forWritingAtPath: serverLog) {
    proc.standardOutput = fh
    proc.standardError = fh
}
do { try proc.run() }
catch { logLine("启动服务失败: \(error)"); fatalError("launch failed") }
logLine("服务已拉起 pid=\(proc.processIdentifier) port=\(port) python=\(py)")

// ---------- 信号：退出时回收子进程（双保险） ----------
var signalSources: [DispatchSourceSignal] = []
func installSignalHandlers() {
    for sig in [SIGTERM, SIGINT, SIGHUP] {
        signal(sig, SIG_IGN)
        let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
        src.setEventHandler {
            logLine("收到信号 \(sig)，回收服务并退出")
            proc.terminate()
            NSApp.terminate(nil)
        }
        src.resume()
        signalSources.append(src)   // 强引用保活，否则变僵尸
    }
}
installSignalHandlers()

// ---------- WKUIDelegate：补齐 alert / confirm / prompt / 文件选择 ----------
// WKWebView **默认不实现**这些面板：alert 无反应、confirm 恒为 false、prompt 恒为 null、
// <input type=file> 点击完全没反应。不设 uiDelegate 的话，页面上的「新建/删除/导入」
// 会全部表现为「点不动」。这是 WKWebView 与浏览器最大的行为差异之一。
class UIDelegate: NSObject, WKUIDelegate {

    private func present(_ alert: NSAlert) -> NSApplication.ModalResponse {
        alert.runModal()
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "好")
        _ = present(a)
        completionHandler()
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "确定")
        a.addButton(withTitle: "取消")
        let r = present(a)
        completionHandler(r == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert()
        a.messageText = prompt
        a.addButton(withTitle: "确定")
        a.addButton(withTitle: "取消")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        field.stringValue = defaultText ?? ""
        a.accessoryView = field
        a.window.initialFirstResponder = field
        let r = present(a)
        completionHandler(r == .alertFirstButtonReturn ? field.stringValue : nil)
    }

    // 关键：<input type="file"> / webkitdirectory 的点击必须由这里处理，否则永远不弹面板
    func webView(_ webView: WKWebView,
                 runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = !parameters.allowsDirectories
        panel.canCreateDirectories = false
        panel.prompt = parameters.allowsDirectories ? "导入文件夹" : "导入"
        let r = panel.runModal()
        completionHandler(r == .OK ? panel.urls : nil)
    }
}

// ---------- 导出：网页 → 原生「存储为」面板 ----------
// WKWebView 不会把 <a download> 当作下载，点下去毫无反应（甚至会把页面导航走）。
// 所以页面通过 messageHandlers.saveExport 把「本机 URL + 建议文件名」递出来，
// 这里弹 NSSavePanel，再由主进程去 127.0.0.1 取数据写入用户选的位置。
final class ExportHandler: NSObject, WKScriptMessageHandler {
    weak var webView: WKWebView?

    func userContentController(_ ucc: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "saveExport" else { return }
        guard let body = message.body as? [String: Any],
              let urlStr = body["url"] as? String,
              let url = URL(string: urlStr),
              let host = url.host, host == "127.0.0.1" || host == "localhost" else {
            logLine("导出请求被忽略：地址不合法")
            return
        }
        let raw = (body["filename"] as? String) ?? ""
        let fname = raw.isEmpty ? "导出.md" : raw

        let panel = NSSavePanel()
        panel.title = "导出"
        panel.prompt = "导出"
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = fname
        if let dl = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first {
            panel.directoryURL = dl
        }
        guard panel.runModal() == .OK, let dest = panel.url else {
            logLine("导出已取消：\(fname)")
            return
        }

        URLSession.shared.downloadTask(with: url) { tmp, _, err in
            guard err == nil, let tmp = tmp else {
                logLine("导出失败：\(err?.localizedDescription ?? "无数据")")
                self.notify("导出失败，请重试")
                return
            }
            do {
                if FileManager.default.fileExists(atPath: dest.path) {
                    try FileManager.default.removeItem(at: dest)
                }
                try FileManager.default.moveItem(at: tmp, to: dest)
                let attrs = try? FileManager.default.attributesOfItem(atPath: dest.path)
                let size = (attrs?[.size] as? NSNumber)?.intValue ?? 0
                logLine("已导出 \(dest.path)（\(size) 字节）")
                self.notify("已导出：\(dest.lastPathComponent)")
            } catch {
                logLine("写入失败：\(error.localizedDescription)")
                self.notify("无法写入所选位置")
            }
        }.resume()
    }

    private func notify(_ msg: String) {
        DispatchQueue.main.async {
            self.webView?.evaluateJavaScript("window.toast && toast(\(ExportHandler.jsString(msg)));",
                                             completionHandler: nil)
        }
    }

    // 把任意字符串安全地嵌进 JS 字面量（引号/反斜杠/中文都不用自己转义）
    static func jsString(_ s: String) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: [s])) ?? Data("[\"\"]".utf8)
        let arr = String(data: data, encoding: .utf8) ?? "[\"\"]"
        return String(arr.dropFirst().dropLast())
    }
}

// ---------- 附件：交给系统默认程序打开 ----------
// 正文里的附件卡片是 assets/xxx.pdf 这样的相对地址。WKWebView 里直接点链接会把
// 整个界面导航到那个文件上（且回不来），所以走 messageHandlers.openFile，
// 由原生用 NSWorkspace 打开 —— 行为和 Finder 里双击一致。
final class OpenFileHandler: NSObject, WKScriptMessageHandler {
    func userContentController(_ ucc: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "openFile" else { return }
        guard let body = message.body as? [String: Any],
              let rel = body["path"] as? String else { return }
        let name = (rel as NSString).lastPathComponent
        // 只允许 assets 目录下的纯文件名，挡掉 ../ 之类的路径穿越
        guard !name.isEmpty, !name.hasPrefix("."), !name.contains("/") else {
            logLine("打开附件被拒绝：\(rel)")
            return
        }
        let url = URL(fileURLWithPath: dataDir)
            .appendingPathComponent("assets")
            .appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            logLine("附件不存在：\(url.path)")
            return
        }
        DispatchQueue.main.async {
            NSWorkspace.shared.open(url)
            logLine("已打开附件 \(url.path)")
        }
    }
}

// ---------- 窗口 ----------
let app = NSApplication.shared
app.setActivationPolicy(.regular)

let win = NSWindow(
    contentRect: NSRect(x: 0, y: 0, width: 1120, height: 740),
    styleMask: [.titled, .closable, .miniaturizable, .resizable],
    backing: .buffered, defer: false)
win.title = "离线笔记本"
win.center()

let webView = WKWebView(frame: win.contentView!.bounds)
webView.autoresizingMask = [.width, .height]
// 不设 uiDelegate → alert/confirm/prompt/文件选择全部失效（按钮"点不动"的真凶）
let uiDelegate = UIDelegate()
webView.uiDelegate = uiDelegate
// 导出通道（网页 → 原生存储为面板）
let exportHandler = ExportHandler()
exportHandler.webView = webView
webView.configuration.userContentController.add(exportHandler, name: "saveExport")
// 附件打开通道（网页 → 原生 NSWorkspace）
let openFileHandler = OpenFileHandler()
webView.configuration.userContentController.add(openFileHandler, name: "openFile")
win.contentView?.addSubview(webView)
// 让网页视图直接成为第一响应者，这样窗口一打开 ⌘A/⌘C/⌘V 就有接收方
win.makeFirstResponder(webView)
// 确保窗口出现在“当前活动 Space”，而不是被全屏应用挤到别的桌面
win.collectionBehavior.insert(.moveToActiveSpace)

// ---------- 菜单栏 ----------
// macOS 的 ⌘C/⌘V/⌘X/⌘A 不是浏览器自己处理的热键，而是「编辑菜单项 → copy:/paste: 动作 →
// 响应链 → WKWebView」。**没有主菜单栏时这些快捷键完全没有投递路径**，表现就是
// 「能不能选中文字？」能选，但按 ⌘C 毫无反应、⌘V 也粘不进去。
// 所以这里的每一项目标都设为 nil（= 交给第一响应者），由 WKWebView / NSTextField 自己接管。
final class MenuActions: NSObject {
    weak var webView: WKWebView?
    @objc func reload(_ sender: Any?) { webView?.reload() }
    @objc func reloadFromOrigin(_ sender: Any?) { webView?.reloadFromOrigin() }
    // 文件 ▸ 导出当前笔记…（⌘E）：转交页面里的导出流程，界面表现与点按钮一致
    @objc func exportNote(_ sender: Any?) {
        webView?.evaluateJavaScript("window.exportCurrentNote && exportCurrentNote();",
                                    completionHandler: nil)
    }
}

let menuActions = MenuActions()
menuActions.webView = webView

func buildMainMenu() {
    let main = NSMenu()

    // --- 应用菜单 ---
    let appItem = NSMenuItem()
    main.addItem(appItem)
    let appMenu = NSMenu()
    appItem.submenu = appMenu
    appMenu.addItem(withTitle: "关于 离线笔记本",
                    action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
                    keyEquivalent: "")
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "隐藏 离线笔记本",
                    action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
    let hideOthers = appMenu.addItem(withTitle: "隐藏其他",
                                     action: #selector(NSApplication.hideOtherApplications(_:)),
                                     keyEquivalent: "h")
    hideOthers.keyEquivalentModifierMask = [.command, .option]
    appMenu.addItem(withTitle: "显示全部",
                    action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
    appMenu.addItem(.separator())
    appMenu.addItem(withTitle: "退出 离线笔记本",
                    action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

    // --- 文件菜单 ---
    let fileItem = NSMenuItem()
    main.addItem(fileItem)
    let file = NSMenu(title: "文件")
    fileItem.submenu = file
    let expItem = NSMenuItem(title: "导出当前笔记…",
                             action: #selector(MenuActions.exportNote(_:)), keyEquivalent: "e")
    expItem.target = menuActions
    file.addItem(expItem)
    file.addItem(.separator())
    let closeItem = NSMenuItem(title: "关闭窗口",
                               action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
    closeItem.target = nil
    file.addItem(closeItem)

    // --- 编辑菜单（复制粘贴的核心）---
    let editItem = NSMenuItem()
    main.addItem(editItem)
    let edit = NSMenu(title: "编辑")
    editItem.submenu = edit

    func actionItem(_ title: String, _ sel: String, _ key: String,
                    _ mask: NSEvent.ModifierFlags = [.command]) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: Selector((sel)), keyEquivalent: key)
        it.keyEquivalentModifierMask = mask
        it.target = nil          // nil = 走响应链，交给当前第一响应者
        return it
    }

    edit.addItem(actionItem("撤销", "undo:", "z"))
    edit.addItem(actionItem("重做", "redo:", "z", [.command, .shift]))
    edit.addItem(.separator())
    edit.addItem(actionItem("剪切", "cut:", "x"))
    edit.addItem(actionItem("复制", "copy:", "c"))
    edit.addItem(actionItem("粘贴", "paste:", "v"))
    edit.addItem(actionItem("粘贴为纯文本", "pasteAsPlainText:", "v", [.command, .option, .shift]))
    edit.addItem(.separator())
    edit.addItem(actionItem("全选", "selectAll:", "a"))

    // --- 显示菜单 ---
    let viewItem = NSMenuItem()
    main.addItem(viewItem)
    let view = NSMenu(title: "显示")
    viewItem.submenu = view
    let reloadItem = NSMenuItem(title: "重新载入",
                                action: #selector(MenuActions.reload(_:)), keyEquivalent: "r")
    reloadItem.target = menuActions
    view.addItem(reloadItem)

    // --- 窗口菜单 ---
    let winItem = NSMenuItem()
    main.addItem(winItem)
    let winMenu = NSMenu(title: "窗口")
    winItem.submenu = winMenu
    winMenu.addItem(withTitle: "最小化",
                    action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
    // 「关闭窗口 ⌘W」放在文件菜单里了，这里不重复，避免同一个快捷键挂两个菜单项

    NSApp.mainMenu = main
    NSApp.windowsMenu = winMenu
}
buildMainMenu()

// 自检：日志里能看到 copy/paste 是否真的由 WKWebView 接管（respondsTo 走响应链）
DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
    let canCopy = webView.tryToPerform(Selector(("copy:")), with: nil)
    let canPaste = webView.tryToPerform(Selector(("paste:")), with: nil)
    let canSelect = webView.tryToPerform(Selector(("selectAll:")), with: nil)
    let menuNames = (NSApp.mainMenu?.items.compactMap { $0.submenu?.title } ?? []).joined(separator: "/")
    logLine("菜单: \(menuNames) | 编辑命令 copy=\(canCopy) paste=\(canPaste) selectAll=\(canSelect)")

    // 自检导出通道：探针查 messageHandler 是否存在，再故意发一个「非本机地址」，
    // 处理器会拒绝并落日志——能拒绝就说明 JS→Swift 这条链路是真的通的。
    webView.evaluateJavaScript(
        "!!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.saveExport)")
    { value, _ in
        let ok = (value as? Bool) ?? ((value as? NSNumber)?.boolValue ?? false)
        logLine("导出通道 saveExport=\(ok)")
        if ok {
            webView.evaluateJavaScript(
                "window.webkit.messageHandlers.saveExport.postMessage({url:'https://example.invalid/x.md', filename:'probe.md'}); 1",
                completionHandler: nil)
        }
    }
    // 自检附件通道：只探测 handler 是否注册（不真的打开文件）
    webView.evaluateJavaScript(
        "!!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.openFile)")
    { value, _ in
        let ok = (value as? Bool) ?? ((value as? NSNumber)?.boolValue ?? false)
        logLine("附件通道 openFile=\(ok)")
    }
}

if waitForServer(port: port) {
    logLine("服务就绪，加载页面")
    webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))
} else {
    logLine("服务启动超时")
    webView.loadHTMLString("<h2>服务启动失败</h2><p>请查看日志：<br>\(logPath)</p>", baseURL: nil)
}

class WinDelegate: NSObject, NSWindowDelegate {
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        logLine("窗口关闭，回收服务并退出")
        proc.terminate()
        NSApp.terminate(nil)
        return true
    }
}
let del = WinDelegate()
win.delegate = del
win.makeKeyAndOrderFront(nil)
win.orderFrontRegardless()

// 延迟到 run loop 启动后再激活，确保从全屏应用/其它 Space 切回前台
DispatchQueue.main.async {
    NSApp.activate(ignoringOtherApps: true)
    win.makeKeyAndOrderFront(nil)
    win.orderFrontRegardless()
}

app.run()
