import qrcode from "qrcode-terminal";

const url = process.argv[2];
if (!url) {
  process.exitCode = 1;
} else {
  console.log(url);
  qrcode.generate(url, { small: true });
}
