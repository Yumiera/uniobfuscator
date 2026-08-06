public class Util {
    public static final double RATE = 1.5;

    public static double scale(double value) {
        double total = value * RATE;
        String label = "scaled";
        System.out.println(label + " " + total);
        return total;
    }

    public static int counter(int start, int step) {
        int value = start;
        value += step;
        String msg = "current";
        System.out.println(msg + "=" + value);
        return value;
    }
}
