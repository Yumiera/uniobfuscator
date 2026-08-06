public class Main {
    public static String greet(String name, String greeting) {
        int x = 5;
        String s = "world" + name;
        for (int i = 0; i < 3; i++) {
            System.out.println(i + " " + s);
        }
        return greeting + ", " + s + " " + x;
    }

    public static int calc(int a, int b) {
        int total = a * b + 100;
        String msg = "result";
        System.out.println(msg + " " + total + " 中文测试");
        return total;
    }

    public static void main(String[] args) {
        System.out.println(greet("Alice", "Hello"));
        System.out.println(calc(3, 4));
    }
}
