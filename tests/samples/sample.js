function greet(name, greeting = "Hello") {
  const x = 5;
  const s = "world" + name;
  for (let i = 0; i < 3; i++) {
    console.log(i, s);
  }
  return `${greeting}, ${s} ${x}`;
}

function calc(a, b) {
  let total = a * b + 100;
  const msg = "result";
  console.log(msg, total, "中文测试");
  return total;
}

class Greeter {
  constructor(prefix) {
    this.prefix = prefix;
  }
  say(who) {
    return this.prefix + " " + who + "!";
  }
}

console.log(greet("Alice"));
console.log(calc(3, 4));
const g = new Greeter("Hi");
console.log(g.say("Bob"));
