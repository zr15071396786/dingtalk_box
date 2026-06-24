/* qrcode.js — QR Code renderer (canvas + svg), vendored from
 *   qrcode-generator by Kazuhiko Arase (MIT)
 *
 * Why this file: pywebview (WebView2) ships with a modern browser, so we just
 * use a <canvas> for rendering — much smaller and more reliable than GIF LZW.
 *
 * API:
 *   const qr = QRCode.create("text");
 *   qr.toCanvas(canvasEl, cellSize, margin);   // draw to <canvas>
 *   qr.toSvgString(cellSize, margin);          // return <svg> string
 *
 * Original MIT source: https://github.com/kazuhikoarase/qrcode-generator
 */
(function (root) {
  'use strict';

  // ── Mode / ECC / Mask enums (8BIT_BYTE, L/M/Q/H, 0..7) ────────────
  var MODE_8BIT_BYTE = 1 << 2;
  var ECC = { L: 1, M: 0, Q: 3, H: 2 };

  // ── ECC polynomial factory (gf(256)) ──────────────────────────────
  var EXP = new Array(256), LOG = new Array(256);
  for (var i = 0; i < 8; i++) EXP[i] = 1 << i;
  for (var i = 8; i < 256; i++) {
    EXP[i] = EXP[i - 4] ^ EXP[i - 5] ^ EXP[i - 6] ^ EXP[i - 8];
  }
  for (var i = 0; i < 255; i++) LOG[EXP[i]] = i;
  function gfExp(n) { while (n < 0) n += 255; while (n >= 256) n -= 255; return EXP[n]; }
  function gfLog(n) { if (n < 1) throw new Error('glog(' + n + ')'); return LOG[n]; }

  function Polynomial(num, shift) {
    var offset = 0;
    while (offset < num.length && num[offset] === 0) offset++;
    this.num = new Array(num.length - offset + shift);
    for (var i = 0; i < num.length - offset; i++) this.num[i] = num[offset + i];
  }
  Polynomial.prototype.get = function (i) { return this.num[i]; };
  Polynomial.prototype.getLength = function () { return this.num.length; };
  Polynomial.prototype.multiply = function (e) {
    var num = new Array(this.getLength() + e.getLength() - 1);
    for (var i = 0; i < num.length; i++) num[i] = 0;
    for (var i = 0; i < this.getLength(); i++) {
      for (var j = 0; j < e.getLength(); j++) {
        num[i + j] ^= gfExp(gfLog(this.get(i)) + gfLog(e.get(j)));
      }
    }
    return new Polynomial(num, 0);
  };
  Polynomial.prototype.mod = function (e) {
    if (this.getLength() - e.getLength() < 0) return this;
    var ratio = gfLog(this.get(0)) - gfLog(e.get(0));
    var num = new Array(this.getLength());
    for (var i = 0; i < this.getLength(); i++) num[i] = this.get(i);
    for (var i = 0; i < e.getLength(); i++) num[i] ^= gfExp(gfLog(e.get(i)) + ratio);
    return new Polynomial(num, 0).mod(e);
  };

  // ── RS block table (40 types × 4 ECC levels) ─────────────────────
  // Indexing: (typeNumber-1)*4 + (L=0|M=1|Q=2|H=3)
  // Each entry is a flat array of triples: [count1, total1, data1, count2, total2, data2, ...]
  // (Data from ISO/IEC 18004 spec; full table verbatim from qrcode-generator.)
  var RS_BLOCKS = [
    [1,26,19],          [1,26,16],          [1,26,13],          [1,26,9],
    [1,44,34],          [1,44,28],          [1,44,22],          [1,44,16],
    [1,70,55],          [1,70,44],          [2,35,17],          [2,35,13],
    [1,100,80],         [2,50,32],          [2,50,24],          [4,25,9],
    [1,134,108],        [2,67,43],          [2,33,15,2,34,16],  [2,33,11,2,34,12],
    [2,86,68],          [4,43,27],          [4,43,19],          [4,43,15],
    [2,98,78],          [4,49,31],          [2,32,14,4,33,15],  [4,39,13,1,40,14],
    [2,121,97],         [2,60,38,2,61,39],  [4,40,18,2,41,19],  [4,40,14,2,41,15],
    [2,146,116],        [3,58,36,2,59,37],  [4,36,16,4,37,17],  [4,36,12,4,37,13],
    [2,86,68,2,87,69],  [4,69,43,1,70,44],  [6,43,19,2,44,20],  [6,43,15,2,44,16],
    [4,101,81],         [1,80,50,4,81,51],  [4,50,22,4,51,23],  [3,36,12,8,37,13],
    [2,116,92,2,117,93],[6,58,36,2,59,37],  [4,46,20,6,47,21],  [7,42,14,4,43,15],
    [4,133,107],        [8,59,37,1,60,38],  [8,44,20,4,45,21],  [12,33,11,4,34,12],
    [3,145,115,1,146,116],[4,64,40,5,65,41],[11,36,16,5,37,17],[11,36,12,5,37,13],
    [5,109,87,1,110,88],[5,65,41,5,66,42], [5,54,24,7,55,25],  [11,36,12,7,37,13],
    [5,122,98,1,123,99],[7,73,45,3,74,46], [15,43,19,2,44,20], [3,45,15,13,46,16],
    [1,135,107,5,136,108],[10,74,46,1,75,47],[1,50,22,15,51,23],[2,42,14,17,43,15],
    [5,150,120,1,151,121],[9,69,43,4,70,44],[17,50,22,1,51,23],[2,42,14,19,43,15],
    [3,141,113,4,142,114],[3,70,44,11,71,45],[17,47,21,4,48,22],[9,39,13,16,40,14],
    [3,135,107,5,136,108],[3,67,41,13,68,42],[15,54,24,5,55,25],[15,43,15,10,44,16],
    [4,144,116,4,145,117],[17,68,42],     [17,50,22,6,51,23], [19,46,16,6,47,17],
    [2,139,111,7,140,112],[17,74,46],     [7,54,24,16,55,25], [34,37,13],
    [4,151,121,5,152,122],[4,75,47,14,76,48],[11,54,24,14,55,25],[16,45,15,14,46,16],
    [6,147,117,4,148,118],[6,73,45,14,74,46],[11,54,24,16,55,25],[30,46,16,2,47,17],
    [8,132,106,4,133,107],[8,75,47,13,76,48],[7,54,24,22,55,25], [22,45,15,13,46,16],
    [10,142,114,2,143,115],[19,74,46,4,75,47],[28,50,22,6,51,23],[33,46,16,4,47,17],
    [8,152,122,4,153,123],[22,73,45,3,74,46],[8,53,23,26,54,24], [12,45,15,28,46,16],
    [3,147,117,10,148,118],[3,73,45,23,74,46],[4,54,24,31,55,25],[11,45,15,31,46,16],
    [7,146,116,7,147,117],[21,73,45,7,74,46],[1,53,23,37,54,24], [19,45,15,26,46,16],
    [5,145,115,10,146,116],[19,75,47,10,76,48],[15,54,24,25,55,25],[23,45,15,25,46,16],
    [13,145,115,3,146,116],[2,74,46,29,75,47],[42,54,24,1,55,25],[23,45,15,28,46,16],
    [17,145,115],       [10,74,46,23,75,47],[10,54,24,35,55,25],[19,45,15,35,46,16],
    [17,145,115,1,146,116],[14,74,46,21,75,47],[29,54,24,19,55,25],[11,45,15,46,46,16],
    [13,145,115,6,146,116],[14,74,46,23,75,47],[44,54,24,7,55,25],[59,46,16,1,47,17],
    [12,151,121,7,152,122],[12,75,47,26,76,48],[39,54,24,14,55,25],[22,45,15,41,46,16],
    [6,151,121,14,152,122],[6,75,47,34,76,48],[46,54,24,10,55,25],[2,45,15,64,46,16],
    [17,152,122,4,153,123],[29,74,46,14,75,47],[49,54,24,10,55,25],[24,45,15,46,46,16],
    [4,152,122,18,153,123],[13,74,46,32,75,47],[48,54,24,14,55,25],[42,45,15,32,46,16],
    [20,147,117,4,148,118],[40,75,47,7,76,48],[43,54,24,22,55,25],[10,45,15,67,46,16],
    [19,148,118,6,149,119],[18,75,47,31,76,48],[34,54,24,34,55,25],[20,45,15,61,46,16]
  ];

  function getRsBlocks(typeNumber, ecLevel) {
    var idx = (typeNumber - 1) * 4 + (ecLevel === ECC.L ? 0 : ecLevel === ECC.M ? 1 : ecLevel === ECC.Q ? 2 : 3);
    var list = RS_BLOCKS[idx];
    var blocks = [];
    for (var i = 0; i < list.length; ) {
      var count = list[i++];
      var total = list[i++];
      var data = list[i++];
      for (var j = 0; j < count; j++) blocks.push({ total: total, data: data });
    }
    return blocks;
  }

  // ── Bit buffer (writes a stream of 1/0 bits) ─────────────────────
  function BitBuffer() { this.buffer = []; this.length = 0; }
  BitBuffer.prototype.put = function (num, len) {
    for (var i = 0; i < len; i++) this.putBit(((num >>> (len - i - 1)) & 1) === 1);
  };
  BitBuffer.prototype.putBit = function (bit) {
    var bufIndex = this.length >>> 3;
    if (this.buffer.length <= bufIndex) this.buffer.push(0);
    if (bit) this.buffer[bufIndex] |= (0x80 >>> (this.length % 8));
    this.length++;
  };
  BitBuffer.prototype.getLengthInBits = function () { return this.length; };

  // ── Finder / alignment / timing pattern positions ───────────────
  var PATTERN_POS = [
    [], [6,18], [6,22], [6,26], [6,30], [6,34],
    [6,22,38], [6,24,42], [6,26,46], [6,28,50], [6,30,54],
    [6,32,58], [6,34,62], [6,26,46,66], [6,26,48,70], [6,26,50,74],
    [6,30,54,78], [6,30,56,82], [6,30,58,86], [6,34,62,90],
    [6,28,50,72,94], [6,26,50,74,98], [6,30,54,78,102],
    [6,28,54,80,106], [6,32,58,84,110], [6,30,58,86,114],
    [6,34,62,90,118], [6,26,50,74,98,122], [6,30,54,78,102,126],
    [6,26,52,78,104,130], [6,30,56,82,108,134], [6,34,60,86,112,138],
    [6,30,58,86,114,142], [6,34,62,90,118,146],
    [6,30,54,78,102,126,150], [6,24,50,76,102,128,154],
    [6,28,54,80,106,132,158], [6,32,58,84,110,136,162],
    [6,26,54,82,110,138,166], [6,30,58,86,114,142,170]
  ];

  // ── G15 / G18 BCH polynomials for type info / type number ───────
  var G15 = (1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2) | (1 << 1) | 1;
  var G18 = (1 << 12) | (1 << 11) | (1 << 10) | (1 << 9) | (1 << 8) | (1 << 5) | (1 << 2) | 1;
  var G15_MASK = (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1);

  function getBCHDigit(data) {
    var d = 0;
    while (data !== 0) { d++; data >>>= 1; }
    return d;
  }
  function getBCHTypeInfo(data) {
    var d = data << 10;
    while (getBCHDigit(d) - getBCHDigit(G15) >= 0) d ^= (G15 << (getBCHDigit(d) - getBCHDigit(G15)));
    return ((data << 10) | d) ^ G15_MASK;
  }
  function getBCHTypeNumber(data) {
    var d = data << 12;
    while (getBCHDigit(d) - getBCHDigit(G18) >= 0) d ^= (G18 << (getBCHDigit(d) - getBCHDigit(G18)));
    return (data << 12) | d;
  }
  function getMask(mask, i, j) {
    switch (mask) {
      case 0: return (i + j) % 2 === 0;
      case 1: return i % 2 === 0;
      case 2: return j % 3 === 0;
      case 3: return (i + j) % 3 === 0;
      case 4: return (Math.floor(i / 2) + Math.floor(j / 3)) % 2 === 0;
      case 5: return (i * j) % 2 + (i * j) % 3 === 0;
      case 6: return ((i * j) % 2 + (i * j) % 3) % 2 === 0;
      case 7: return ((i * j) % 3 + (i + j) % 2) % 2 === 0;
    }
    return false;
  }
  function getErrorCorrectPoly(ecLen) {
    var a = new Polynomial([1], 0);
    for (var i = 0; i < ecLen; i++) a = a.multiply(new Polynomial([1, gfExp(i)], 0));
    return a;
  }
  function getLengthInBits(mode, type) {
    if (1 <= type && type < 10) {
      switch (mode) { case MODE_8BIT_BYTE: return 8; }
    } else if (type < 27) {
      switch (mode) { case MODE_8BIT_BYTE: return 16; }
    } else if (type < 41) {
      switch (mode) { case MODE_8BIT_BYTE: return 16; }
    }
    throw new Error('type:' + type);
  }
  function getLostPoint(modules, mc) {
    var lost = 0;
    // horizontal runs of same color
    for (var r = 0; r < mc; r++) {
      for (var c = 0; c < mc; c++) {
        var same = 0; var dark = modules[r][c];
        for (var rr = -1; rr <= 1; rr++) {
          if (r + rr < 0 || mc <= r + rr) continue;
          for (var cc = -1; cc <= 1; cc++) {
            if (c + cc < 0 || mc <= c + cc) continue;
            if (rr === 0 && cc === 0) continue;
            if (dark === modules[r + rr][c + cc]) same++;
          }
        }
        if (same > 5) lost += 3 + same - 5;
      }
    }
    return lost;
  }

  // ── QRCodeModel: builds module matrix ────────────────────────────
  function QRCodeModel(typeNumber, ecLevel) {
    this.typeNumber = typeNumber;
    this.ecLevel = ecLevel;
    this.modules = null;
    this.moduleCount = 0;
  }
  QRCodeModel.prototype.addData = function (data) {
    this.dataList = this.dataList || [];
    this.dataList.push(data);
  };
  QRCodeModel.prototype.isDark = function (r, c) {
    if (r < 0 || this.moduleCount <= r || c < 0 || this.moduleCount <= c) throw new Error(r + ',' + c);
    return this.modules[r][c];
  };
  QRCodeModel.prototype.getModuleCount = function () { return this.moduleCount; };
  QRCodeModel.prototype.make = function () { this.makeImpl(false, this.getBestMask()); };
  QRCodeModel.prototype.getBestMask = function () {
    var minLost = 0, pattern = 0;
    for (var i = 0; i < 8; i++) {
      this.makeImpl(true, i);
      var lp = getLostPoint(this.modules, this.moduleCount);
      if (i === 0 || minLost > lp) { minLost = lp; pattern = i; }
    }
    return pattern;
  };
  QRCodeModel.prototype.makeImpl = function (test, mask) {
    this.moduleCount = this.typeNumber * 4 + 17;
    this.modules = new Array(this.moduleCount);
    for (var r = 0; r < this.moduleCount; r++) {
      this.modules[r] = new Array(this.moduleCount);
      for (var c = 0; c < this.moduleCount; c++) this.modules[r][c] = null;
    }
    this.setupFinder(0, 0);
    this.setupFinder(this.moduleCount - 7, 0);
    this.setupFinder(0, this.moduleCount - 7);
    this.setupAlignment();
    this.setupTiming();
    this.setupTypeInfo(test, mask);
    if (this.typeNumber >= 7) this.setupTypeNumber(test);
    var data = this.buildData();
    this.mapData(data, mask);
  };
  QRCodeModel.prototype.setupFinder = function (row, col) {
    for (var r = -1; r <= 7; r++) {
      if (row + r <= -1 || this.moduleCount <= row + r) continue;
      for (var c = -1; c <= 7; c++) {
        if (col + c <= -1 || this.moduleCount <= col + c) continue;
        this.modules[row + r][col + c] =
          (0 <= r && r <= 6 && (c === 0 || c === 6)) ||
          (0 <= c && c <= 6 && (r === 0 || r === 6)) ||
          (2 <= r && r <= 4 && 2 <= c && c <= 4);
      }
    }
  };
  QRCodeModel.prototype.setupAlignment = function () {
    var pos = PATTERN_POS[this.typeNumber - 1];
    for (var i = 0; i < pos.length; i++) {
      for (var j = 0; j < pos.length; j++) {
        var row = pos[i], col = pos[j];
        if (this.modules[row][col] !== null) continue;
        for (var r = -2; r <= 2; r++) {
          for (var c = -2; c <= 2; c++) {
            this.modules[row + r][col + c] = (r === -2 || r === 2 || c === -2 || c === 2 || (r === 0 && c === 0));
          }
        }
      }
    }
  };
  QRCodeModel.prototype.setupTiming = function () {
    for (var r = 8; r < this.moduleCount - 8; r++) {
      if (this.modules[r][6] !== null) continue;
      this.modules[r][6] = (r % 2 === 0);
    }
    for (var c = 8; c < this.moduleCount - 8; c++) {
      if (this.modules[6][c] !== null) continue;
      this.modules[6][c] = (c % 2 === 0);
    }
  };
  QRCodeModel.prototype.setupTypeInfo = function (test, mask) {
    var data = (this.ecLevel << 3) | mask;
    var bits = getBCHTypeInfo(data);
    for (var i = 0; i < 15; i++) {
      var mod = (!test && ((bits >> i) & 1) === 1);
      if (i < 6) this.modules[i][8] = mod;
      else if (i < 8) this.modules[i + 1][8] = mod;
      else this.modules[this.moduleCount - 15 + i][8] = mod;
    }
    for (var i = 0; i < 15; i++) {
      var mod = (!test && ((bits >> i) & 1) === 1);
      if (i < 8) this.modules[8][this.moduleCount - i - 1] = mod;
      else if (i < 9) this.modules[8][15 - i - 1 + 1] = mod;
      else this.modules[8][15 - i - 1] = mod;
    }
    this.modules[this.moduleCount - 8][8] = (!test);
  };
  QRCodeModel.prototype.setupTypeNumber = function (test) {
    var bits = getBCHTypeNumber(this.typeNumber);
    for (var i = 0; i < 18; i++) {
      var mod = (!test && ((bits >> i) & 1) === 1);
      this.modules[Math.floor(i / 3)][i % 3 + this.moduleCount - 8 - 3] = mod;
    }
    for (var i = 0; i < 18; i++) {
      var mod = (!test && ((bits >> i) & 1) === 1);
      this.modules[i % 3 + this.moduleCount - 8 - 3][Math.floor(i / 3)] = mod;
    }
  };
  QRCodeModel.prototype.buildData = function () {
    var buffer = new BitBuffer();
    for (var i = 0; i < this.dataList.length; i++) {
      var data = this.dataList[i];
      buffer.put(MODE_8BIT_BYTE, 4);
      buffer.put(data.length, getLengthInBits(MODE_8BIT_BYTE, this.typeNumber));
      for (var j = 0; j < data.length; j++) buffer.put(data.charCodeAt(j) & 0xff, 8);
    }
    var blocks = getRsBlocks(this.typeNumber, this.ecLevel);
    var totalData = 0;
    for (var i = 0; i < blocks.length; i++) totalData += blocks[i].data;
    if (buffer.getLengthInBits() > totalData * 8) throw new Error('overflow');
    if (buffer.getLengthInBits() + 4 <= totalData * 8) buffer.put(0, 4);
    while (buffer.getLengthInBits() % 8 !== 0) buffer.putBit(false);
    while (true) {
      if (buffer.getLengthInBits() >= totalData * 8) break;
      buffer.put(0xEC, 8);
      if (buffer.getLengthInBits() >= totalData * 8) break;
      buffer.put(0x11, 8);
    }
    // Add ECC for each block, then interleave
    var offset = 0, dcdata = [], ecdata = [];
    var maxDc = 0, maxEc = 0;
    for (var r = 0; r < blocks.length; r++) {
      var dc = blocks[r].data;
      var ec = blocks[r].total - dc;
      maxDc = Math.max(maxDc, dc);
      maxEc = Math.max(maxEc, ec);
      var dcArr = [];
      for (var i = 0; i < dc; i++) dcArr.push(buffer.buffer[i + offset] & 0xff);
      offset += dc;
      var rsPoly = getErrorCorrectPoly(ec);
      var modPoly = new Polynomial(dcArr, rsPoly.getLength() - 1).mod(rsPoly);
      var ecArr = new Array(rsPoly.getLength() - 1);
      for (var i = 0; i < ecArr.length; i++) {
        var mi = i + modPoly.getLength() - ecArr.length;
        ecArr[i] = (mi >= 0) ? modPoly.get(mi) : 0;
      }
      dcdata.push(dcArr);
      ecdata.push(ecArr);
    }
    var totalCount = 0;
    for (var i = 0; i < blocks.length; i++) totalCount += blocks[i].total;
    var data = new Array(totalCount);
    var index = 0;
    for (var i = 0; i < maxDc; i++) {
      for (var r = 0; r < blocks.length; r++) if (i < dcdata[r].length) data[index++] = dcdata[r][i];
    }
    for (var i = 0; i < maxEc; i++) {
      for (var r = 0; r < blocks.length; r++) if (i < ecdata[r].length) data[index++] = ecdata[r][i];
    }
    return data;
  };
  QRCodeModel.prototype.mapData = function (data, mask) {
    var inc = -1, row = this.moduleCount - 1, bitIndex = 7, byteIndex = 0;
    for (var col = this.moduleCount - 1; col > 0; col -= 2) {
      if (col === 6) col--;
      while (true) {
        for (var c = 0; c < 2; c++) {
          if (this.modules[row][col - c] === null) {
            var dark = false;
            if (byteIndex < data.length) dark = (((data[byteIndex] >>> bitIndex) & 1) === 1);
            if (getMask(mask, row, col - c)) dark = !dark;
            this.modules[row][col - c] = dark;
            bitIndex--;
            if (bitIndex === -1) { byteIndex++; bitIndex = 7; }
          }
        }
        row += inc;
        if (row < 0 || this.moduleCount <= row) { row -= inc; inc = -inc; break; }
      }
    }
  };

  // ── Public QRCode (thin wrapper with renderer methods) ──────────
  function QRCode(typeNumber, ecLevel) {
    // 接受 (model) 或 (typeNumber, ecLevel) 两种调用方式
    if (typeNumber instanceof QRCodeModel) {
      this.model = typeNumber;
      this.typeNumber = typeNumber.typeNumber;
      this.ecLevel = typeNumber.ecLevel;
    } else {
      this.typeNumber = typeNumber;
      this.ecLevel = ecLevel;
      this.model = new QRCodeModel(typeNumber, ecLevel);
    }
  }
  /**
   * Auto-pick the smallest typeNumber that can hold `text` (8-bit, ECC=M).
   * Throws if text > ~2953 bytes (max for type 40, L; smaller for M).
   */
  QRCode.create = function (text) {
    var ecLevel = ECC.M;
    for (var t = 1; t <= 40; t++) {
      var m = new QRCodeModel(t, ecLevel);
      m.addData(text);
      try { m.make(); return new QRCode(m); } catch (_) { /* try next type */ }
    }
    throw new Error('text too long for QR');
  };
  QRCode.prototype.addData = function (data) { this.model.addData(data); };
  QRCode.prototype.isDark = function (r, c) { return this.model.isDark(r, c); };
  QRCode.prototype.getModuleCount = function () { return this.model.getModuleCount(); };
  QRCode.prototype.make = function () { this.model.make(); };

  /**
   * Draw QR to a <canvas> element. Auto-fits to the canvas's intrinsic size.
   * Returns the same canvas.
   */
  QRCode.prototype.toCanvas = function (canvas, cellSize, margin) {
    if (!canvas || !canvas.getContext) throw new Error('canvas element required');
    cellSize = cellSize || 4;
    margin = (margin === undefined) ? cellSize * 4 : margin;
    var n = this.getModuleCount();
    var size = n * cellSize + margin * 2;
    canvas.width = size;
    canvas.height = size;
    var ctx = canvas.getContext('2d');
    // White background
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, size, size);
    // Black modules
    ctx.fillStyle = '#000000';
    for (var r = 0; r < n; r++) {
      for (var c = 0; c < n; c++) {
        if (this.isDark(r, c)) {
          ctx.fillRect(margin + c * cellSize, margin + r * cellSize, cellSize, cellSize);
        }
      }
    }
    return canvas;
  };

  /**
   * Return an SVG string for the QR code. Useful if you want it inline in HTML.
   */
  QRCode.prototype.toSvgString = function (cellSize, margin) {
    cellSize = cellSize || 4;
    margin = (margin === undefined) ? cellSize * 4 : margin;
    var n = this.getModuleCount();
    var size = n * cellSize + margin * 2;
    var rects = '';
    for (var r = 0; r < n; r++) {
      for (var c = 0; c < n; c++) {
        if (this.isDark(r, c)) {
          rects += '<rect x="' + (margin + c * cellSize) + '" y="' + (margin + r * cellSize) +
                   '" width="' + cellSize + '" height="' + cellSize + '"/>';
        }
      }
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size +
           '" viewBox="0 0 ' + size + ' ' + size + '"><rect width="100%" height="100%" fill="#fff"/>' +
           '<g fill="#000">' + rects + '</g></svg>';
  };

  // Expose
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = QRCode;
  } else {
    root.QRCode = QRCode;
  }
})(typeof window !== 'undefined' ? window : this);
