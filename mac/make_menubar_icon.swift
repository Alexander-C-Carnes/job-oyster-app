// Template oyster for the menu bar (same mark as make_icon.swift / index.html, no teal tile).
//   swift mac/make_menubar_icon.swift build/MenuBarIcon.png
import AppKit

let size: CGFloat = 36   // @2x of the 18pt status item
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "MenuBarIcon.png"
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

let flip = NSAffineTransform()
flip.translateX(by: 0, yBy: size)
flip.scaleX(by: 1, yBy: -1)
flip.concat()

let src = CGRect(x: 220, y: 250, width: 600, height: 600)
let t = NSAffineTransform()
t.scaleX(by: size / src.width, yBy: size / src.height)
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

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
