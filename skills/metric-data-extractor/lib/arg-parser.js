function parseArgs(argv) {
  const result = {};

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) {
      continue;
    }

    const key = token
      .slice(2)
      .replace(/-([a-z])/g, (_, char) => char.toUpperCase());

    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      result[key] = true;
      continue;
    }

    result[key] = next;
    index += 1;
  }

  return result;
}

function printJsonError(error, extra = {}) {
  console.error(
    JSON.stringify(
      {
        error,
        ...extra,
      },
      null,
      2,
    ),
  );
}

module.exports = {
  parseArgs,
  printJsonError,
};
