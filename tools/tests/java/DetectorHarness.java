import com.alokg.ffmenu.vision.Detector;

import java.util.List;
import java.util.Random;

/**
 * Test harness for the vision detector, not shipped in the APK.
 *
 * <p>Builds frames whose answer is known - a bar of a chosen colour at a chosen
 * place - and prints what the detector found, so the Python side can assert on
 * it. No Android, no device.
 *
 * <pre>
 *   DetectorHarness red           a red bar and a green bar, looking for red
 *   DetectorHarness pick          a bar of one exact colour, MODE_PICK
 *   DetectorHarness two           two red bars, nearest-to-centre ordering
 *   DetectorHarness noise         random pixels, must find nothing
 *   DetectorHarness thin          a bar one pixel too short, must be rejected
 *   DetectorHarness bands         a bar above the band, must be ignored
 * </pre>
 */
public final class DetectorHarness {

    private static final int W = 640;
    private static final int H = 360;

    public static void main(String[] args) {
        String mode = args[0];
        int[] frame = new int[W * H];
        Detector.Params p = new Detector.Params();

        switch (mode) {
            case "red": {
                java.util.Arrays.fill(frame, 0xFF101014);
                bar(frame, 300, 120, 60, 6, 0xFFB02020);
                bar(frame, 100, 200, 60, 6, 0xFF20B020);
                p.mode = Detector.MODE_DOMINANT;
                p.channel = 0;
                break;
            }
            case "pick": {
                java.util.Arrays.fill(frame, 0xFF101014);
                bar(frame, 288, 140, 64, 5, 0xFF3030FF);
                bar(frame, 100, 200, 64, 5, 0xFF30FF30);
                p.mode = Detector.MODE_PICK;
                p.rgb = 0xFF3030FF;
                p.tol = 40;
                break;
            }
            case "two": {
                java.util.Arrays.fill(frame, 0xFF101014);
                bar(frame, 40, 100, 60, 6, 0xFFB02020);
                bar(frame, 500, 300, 60, 6, 0xFFB02020);
                p.mode = Detector.MODE_DOMINANT;
                p.channel = 0;
                break;
            }
            case "noise": {
                Random rnd = new Random(7);
                for (int i = 0; i < frame.length; i++) {
                    frame[i] = 0xFF000000 | rnd.nextInt(0xFFFFFF);
                }
                p.mode = Detector.MODE_DOMINANT;
                p.channel = 0;
                break;
            }
            case "thin": {
                java.util.Arrays.fill(frame, 0xFF101014);
                bar(frame, 300, 120, 60, 2, 0xFFB02020);
                p.mode = Detector.MODE_DOMINANT;
                p.channel = 0;
                break;
            }
            case "bands": {
                java.util.Arrays.fill(frame, 0xFF101014);
                bar(frame, 300, 20, 60, 6, 0xFFB02020);
                bar(frame, 300, 250, 60, 6, 0xFFB02020);
                p.mode = Detector.MODE_DOMINANT;
                p.channel = 0;
                p.bandTop = 400;
                break;
            }
            default:
                System.out.println("UNKNOWN " + mode);
                System.exit(2);
        }

        List<Detector.Blob> blobs = Detector.detect(frame, W, H, p);
        if (blobs.isEmpty()) {
            System.out.println("NONE");
        }
        for (Detector.Blob b : blobs) {
            System.out.println("BLOB " + b.x + " " + b.y + " " + b.w + " " + b.h
                    + " " + b.pixels + " " + b.cx() + " " + b.cy());
        }
    }

    private static void bar(int[] frame, int x, int y, int w, int h, int rgb) {
        for (int j = y; j < y + h; j++) {
            for (int i = x; i < x + w; i++) {
                frame[j * W + i] = rgb;
            }
        }
    }
}