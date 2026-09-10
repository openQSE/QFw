#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <errno.h>
#include <unistd.h>
#include <getopt.h>
#include <string.h>
#include <strings.h>
#include <sys/time.h>
#include <iostream>
#include <fstream>
#include <numeric>
#include "xacc.hpp"
#include "exatn.hpp"

static inline void print_help(void)
{
	fprintf(stderr, "usage: circuit_runner -c <compiler> -q <qasm file> -b <qubits> -s <shots> [--verbose]\n");
}

int main (int argc, char **argv)
{
	char *xacc_argv[1];
	int cOpt;
	char *qasm_file = NULL, *compiler_type = NULL;
	int num_qubits = 0, num_shots = 0;
	bool verbose = false;
	char result_file[1024];

	xacc_argv[0] = argv[0];

	/* If followed by a ':', the option requires an argument*/
	const char *const short_options = "c:q:b:s:hv";
	const struct option long_options[] = {
		{.name = "qasm", .has_arg = required_argument, .val = 'q'},
		{.name = "compiler", .has_arg = required_argument, .val = 'c'},
		{.name = "qubits", .has_arg = required_argument, .val = 'b'},
		{.name = "shots", .has_arg = required_argument, .val = 's'},
		{.name = "verbose", .has_arg = no_argument, .val = 'v'},
		{.name = "help", .has_arg = no_argument, .val = 'h'},
		{NULL, 0, NULL, 0}
	};

	/* sanity check */
	if (argc < 1) {
		print_help();
		exit(1);
	}

	/*now process command line arguments*/
	if (argc > 1) {
		while ((cOpt = getopt_long(argc, argv,
					   short_options,
					   long_options,
					   NULL)) != -1) {
			switch (cOpt) {
			case 'q':
				qasm_file = optarg;
				break;
			case 'c':
				compiler_type = optarg;
				break;
			case 'b':
				num_qubits = atoi(optarg);
				break;
			case 's':
				num_shots = atoi(optarg);
				break;
			case 'v':
				verbose = true;
				break;
			default:
				print_help();
				exit(2);
				break;
			}
		}
	}

	if (!qasm_file || !compiler_type || num_shots == 0 || num_qubits == 0) {
		print_help();
		exit(3);
	}

	// Initialize the XACC Framework
	xacc::Initialize(1, xacc_argv);
	xacc::set_verbose(verbose);
	auto qpu = xacc::getAccelerator("tnqvm", {
		std::make_pair("tnqvm-visitor", "exatn-mps"),
		std::make_pair("shots", num_shots),
	});

	std::ifstream inFile;
	inFile.open(qasm_file);
	if (!inFile.is_open()) {
		std::cerr << "Error opening file: " << qasm_file << std::endl;
		exit(4);
	}
	std::stringstream strStream;
	strStream << inFile.rdbuf();
	std::string qasm_circuit = strStream.str();

	// Create a Program
	auto qubitReg = xacc::qalloc(num_qubits);
	auto compiler = xacc::getCompiler(compiler_type);
	auto ir = compiler->compile(qasm_circuit, qpu);

	// Request the quantum kernel representing
	// the above source code
	auto program = ir->getComposites()[0];
	qpu->execute(qubitReg, program);

	int process_rank = exatn::getProcessRank();
	if (process_rank == 0) {
		std::string strResult = qubitReg->toString();
		qubitReg->print();

		sprintf(result_file, "%s.result", qasm_file);

		std::ofstream outputFile;
		outputFile.open(result_file);

		if (outputFile.is_open()) {
			outputFile << strResult << std::endl;
			outputFile.close();
		} else {
			std::cerr << "Error: Unable to open file for writing." << std::endl;
		}
	}

	// Finalize the XACC Framework
	xacc::Finalize();

	return 0;
}
