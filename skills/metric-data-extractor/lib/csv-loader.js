const fs = require('fs');
const path = require('path');
const Papa = require('papaparse');

function readCsv(relativePath) {
  const absolutePath = path.join(__dirname, '..', relativePath);
  const content = fs.readFileSync(absolutePath, 'utf8');
  const parsed = Papa.parse(content, {
    header: true,
    skipEmptyLines: true,
  });

  return parsed.data;
}

module.exports = {
  readCsv,
};
