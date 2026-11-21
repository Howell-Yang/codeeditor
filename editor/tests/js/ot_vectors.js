/*
 * Cross-checks static/js/ot.js against vectors produced by editor/ot.py.
 * Usage: node ot_vectors.js <vectors.json>
 */
'use strict';
var fs = require('fs');
var path = require('path');
var ot = require(path.join(__dirname, '..', '..', 'static', 'js', 'ot.js'));
var TextOperation = ot.TextOperation;

var vectors = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
var failures = 0;

function check(index, what, actual, expected) {
  var a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) {
    failures++;
    if (failures <= 10) {
      console.error('case ' + index + ': ' + what + '\n  js:     ' + a + '\n  python: ' + e);
    }
  }
}

vectors.forEach(function (vector, index) {
  var a = TextOperation.fromJSON(vector.a);
  var b = TextOperation.fromJSON(vector.b);
  check(index, 'canonical form of a', a.toJSON(), vector.a);
  check(index, 'apply a', a.apply(vector.doc), vector.after_a);
  check(index, 'apply b', b.apply(vector.doc), vector.after_b);
  var pair = TextOperation.transform(a, b);
  check(index, "a'", pair[0].toJSON(), vector.a_prime);
  check(index, "b'", pair[1].toJSON(), vector.b_prime);
  check(index, 'converged text', pair[1].apply(a.apply(vector.doc)), vector.merged);
  check(index, 'converged text (other order)', pair[0].apply(b.apply(vector.doc)), vector.merged);
  check(index, 'compose', a.compose(pair[1]).toJSON(), vector.composed);
  check(index, 'hash', ot.textHash(vector.doc), vector.hash);
  check(index, 'diff', ot.diffOperation(vector.doc, vector.merged).apply(vector.doc), vector.merged);
  vector.indexes.forEach(function (item) {
    check(index, 'transformIndex ' + item[0], a.transformIndex(item[0], false), item[1]);
    check(index, 'transformIndex (before) ' + item[0], a.transformIndex(item[0], true), item[2]);
  });
});

if (failures) {
  console.error(failures + ' mismatches');
  process.exit(1);
}
console.log('OK ' + vectors.length);
