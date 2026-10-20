// Test-only connected socket pair; production still opens its exact Unix path.
const net = require('node:net');
const fd = Number(process.env.PILOT_TEST_FD);
delete process.env.PILOT_TEST_FD;
net.createConnection = () => new net.Socket({fd, readable:true, writable:true});
const script = process.argv[2];
process.argv.splice(1,1);
require(script);
