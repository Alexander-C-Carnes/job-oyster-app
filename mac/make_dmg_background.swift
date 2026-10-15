// Draws the DMG window's background (an arrow from the app to Applications, and what to do) at 1x and 2x:
//   swift make_dmg_background.swift out.png out@2x.png [--open-anyway]
// --open-anyway adds the one-time Privacy & Security step, for builds that aren't notarized.
// The size and the arrow match the window and icon places in mac/dmg_settings.py. Finder counts the title bar
// in the window's height, so the picture runs taller than what shows and nothing sits in its last 40 points.
import AppKit

let args = CommandLine.arguments
let openAnyway = args.contains("--open-anyway")
let outs = args.dropFirst().filter { !$0.hasPrefix("--") }
let width: CGFloat = 660, height: CGFloat = 440

let teal = NSColor(srgbRed: 0x0E / 255, green: 0x7C / 255, blue: 0x7B / 255, alpha: 1)   // the icon's tile
let ink = NSColor(srgbRed: 0x1F / 255, green: 0x1B / 255, blue: 0x1A / 255, alpha: 1)
let muted = NSColor(srgbRed: 0x6E / 255, green: 0x66 / 255, blue: 0x63 / 255, alpha: 1)

func centered(_ text: String, y: CGFloat, size: CGFloat, weight: NSFont.Weight, color: NSColor) {
    let para = NSMutableParagraphStyle()
    para.alignment = .center
    let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: size, weight: weight),
                                                .foregroundColor: color, .paragraphStyle: para]
    NSAttributedString(string: text, attributes: attrs).draw(in: NSRect(x: 0, y: y, width: width, height: size * 1.5))
}

func draw() {
    // A warm off-white, a touch lighter at the top.
    NSGradient(starting: NSColor(srgbRed: 1, green: 0.992, blue: 0.984, alpha: 1),
               ending: NSColor(srgbRed: 0.965, green: 0.945, blue: 0.933, alpha: 1))!
        .draw(in: NSRect(x: 0, y: 0, width: width, height: height), angle: -90)

    // The arrow, between the icons (centres at 170 and 490, 165 from the top).
    let y = height - 165
    let shaft = NSBezierPath()
    shaft.move(to: NSPoint(x: 268, y: y))
    shaft.line(to: NSPoint(x: 384, y: y))
    shaft.lineWidth = 5
    shaft.lineCapStyle = .round
    shaft.setLineDash([0.1, 13], count: 2, phase: 0)
    teal.withAlphaComponent(0.55).setStroke()
    shaft.stroke()
    let head = NSBezierPath()
    head.move(to: NSPoint(x: 380, y: y + 15))
    head.line(to: NSPoint(x: 396, y: y))
    head.line(to: NSPoint(x: 380, y: y - 15))
    head.lineWidth = 5
    head.lineCapStyle = .round
    head.lineJoinStyle = .round
    teal.setStroke()
    head.stroke()

    // The text, under the icons' names (y is each line's bottom, so 316 is 316 points from the top).
    centered("Drag Job Oyster into Applications", y: height - 316, size: 17, weight: .semibold, color: ink)
    centered("Then open it from your Applications folder.", y: height - 340, size: 13, weight: .regular, color: muted)
    if openAnyway {
        centered("If macOS says it can't check the app: System Settings › Privacy & Security › Open Anyway (once).",
                 y: height - 378, size: 11, weight: .regular, color: muted)
    }
}

for (i, out) in outs.enumerated() {
    let scale = CGFloat(i + 1)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(width * scale), pixelsHigh: Int(height * scale),
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: width, height: height)     // 72 dpi at 1x, 144 at 2x
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    draw()
    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
}
