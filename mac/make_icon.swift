// Draws the app icon (Side Clam: an oyster seen from the side, its lid swung open on the hinge and a pearl in its
// mouth) as a 1024px PNG: swift make_icon.swift out.png. The web app's logo is the same drawing (index.html).
import AppKit

let size: CGFloat = 1024
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size), bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

// Draw in the SVG's coordinates (y down), so the numbers match index.html's logo.
let flip = NSAffineTransform()
flip.translateX(by: 0, yBy: size)
flip.scaleX(by: 1, yBy: -1)
flip.concat()

let teal = NSColor(srgbRed: 0x0E / 255, green: 0x7C / 255, blue: 0x7B / 255, alpha: 1)
let shell = NSColor(srgbRed: 0xF4 / 255, green: 0xEB / 255, blue: 0xDD / 255, alpha: 1)
let pearl = NSColor(srgbRed: 0xFF / 255, green: 0x8A / 255, blue: 0x65 / 255, alpha: 1)

teal.setFill()
NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 185, yRadius: 185).fill()

// A bowl from the hinge at the left to the lip at the right: M250 y Q520 y±270 800 y Z.
func bowl(_ y: CGFloat, bulge: CGFloat) -> NSBezierPath {
    let p = NSBezierPath()
    p.move(to: NSPoint(x: 250, y: y))
    p.curve(to: NSPoint(x: 800, y: y),   // the quadratic Q520,y+bulge as a cubic
            controlPoint1: NSPoint(x: 250 + 2.0 / 3 * 270, y: y + 2.0 / 3 * bulge),
            controlPoint2: NSPoint(x: 800 - 2.0 / 3 * 280, y: y + 2.0 / 3 * bulge))
    p.close()
    return p
}

shell.setFill()
bowl(560, bulge: 270).fill()                       // the cup
NSGraphicsContext.saveGraphicsState()              // the lid, swung open 24° on the hinge
let lid = NSAffineTransform()
lid.translateX(by: 250, yBy: 548)
lid.rotate(byDegrees: -24)
lid.translateX(by: -250, yBy: -548)
lid.concat()
bowl(536, bulge: -270).fill()
NSGraphicsContext.restoreGraphicsState()

pearl.setFill()
NSBezierPath(ovalIn: NSRect(x: 600 - 60, y: 510 - 60, width: 120, height: 120)).fill()

NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
