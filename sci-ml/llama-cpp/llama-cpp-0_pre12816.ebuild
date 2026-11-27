# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

PYTHON_COMPAT=( python3_{12..15} )
ROCM_VERSION="6.3"

inherit cmake cuda linux-info python-any-r1 rocm toolchain-funcs

TINY_LLAMAS_COMMIT="99dd1a73db5a37100bd4ae633f4cfce6560e1567"

DESCRIPTION="LLM inference in C/C++"
HOMEPAGE="https://github.com/ggml-org/llama.cpp"

if [[ ${PV} == *9999* ]]; then
	inherit git-r3
	EGIT_REPO_URI="https://github.com/ggml-org/llama.cpp.git"
else
	MY_PV="b${PV#0_pre}"
	SRC_URI="
		https://github.com/ggml-org/llama.cpp/archive/refs/tags/${MY_PV}.tar.gz -> ${P}.tar.gz
		webui? (
			https://github.com/ggml-org/llama.cpp/releases/download/${MY_PV}/llama-${MY_PV}-ui.tar.gz -> ${P}-ui.tar.gz
		)
	"
	S="${WORKDIR}/llama.cpp-${MY_PV}"
	KEYWORDS="~amd64"
fi

SRC_URI+="
	examples? (
		https://huggingface.co/ggml-org/tiny-llamas/resolve/${TINY_LLAMAS_COMMIT}/stories15M-q4_0.gguf
			-> ggml-org_models_tinyllamas_stories15M-q4_0-${TINY_LLAMAS_COMMIT}.gguf
	)
"

LICENSE="MIT"
SLOT="0"

# Optional CUDA tuning environment variables:
#   LLAMA_CPP_CUDA_ARCHITECTURES (or standard CUDAARCHS):
#     "auto" (default when USE="native cuda") — auto-detects local NVIDIA GPUs
#       in pkg_setup (as root, outside build sandbox) via nvidia-smi,
#       __nvcc_device_query, or /proc/driver/nvidia/gpus/*/information.
#     "12.0" / "sm_120" / "120a-real" / "8.9;12.0" — explicit target(s).
#     "all" / "all-major" — passed directly to CMake.
#   LLAMA_CPP_CUDA_FA_QUANTS:
#     FlashAttention K-V quant combinations (default: "all" for max performance).
#   LLAMA_CPP_CUDA_COMPRESSION_MODE:
#     CUDA >= 12.8 fatbin compression: none, speed, balance, size
#     (default: "speed" when USE="native", else "size").
LLAMA_CPP_CUDA_ARCHITECTURES="${LLAMA_CPP_CUDA_ARCHITECTURES:-}"
LLAMA_CPP_CUDA_FA_QUANTS="${LLAMA_CPP_CUDA_FA_QUANTS:-all}"
LLAMA_CPP_CUDA_COMPRESSION_MODE="${LLAMA_CPP_CUDA_COMPRESSION_MODE:-}"

X86_CPU_FLAGS=(
	sse4_2
	avx
	avx_vnni
	avx2
	bmi2
	avx512f avx512cd avx512vl avx512dq avx512bw
	avx512vbmi
	avx512_vnni
	avx512_bf16
	fma3
	f16c
	amx_tile
	amx_int8
	amx_bf16
)
CPU_FLAGS=(
	"${X86_CPU_FLAGS[@]/#/cpu_flags_x86_}"
)

IUSE="
	blis +cpu-repack cuda examples flexiblas +lto +native openblas
	opencl +openmp rocm rpc +server vulkan +webui wmma
	${CPU_FLAGS[*]}
"

unset X86_CPU_FLAGS CPU_FLAGS

REQUIRED_USE="
	?? ( openblas blis flexiblas )
	rocm? ( ${ROCM_REQUIRED_USE} )
	wmma? ( rocm )
	webui? ( server )
"

CDEPEND="
	dev-libs/openssl:=
	openblas? ( sci-libs/openblas:= )
	blis? ( sci-libs/blis:= )
	flexiblas? ( sci-libs/flexiblas:= )
	rocm? (
		>=dev-util/hip-${ROCM_VERSION}:=
		>=sci-libs/hipBLAS-${ROCM_VERSION}:=
		>=sci-libs/rocBLAS-${ROCM_VERSION}:=
		wmma? ( >=sci-libs/rocWMMA-${ROCM_VERSION}:= )
	)
	cuda? ( dev-util/nvidia-cuda-toolkit:= )
"
DEPEND="${CDEPEND}
	opencl? ( dev-util/opencl-headers )
	vulkan? (
		dev-util/spirv-headers
		dev-util/vulkan-headers
	)
"
RDEPEND="${CDEPEND}
	opencl? ( dev-libs/opencl-icd-loader )
	vulkan? ( media-libs/vulkan-loader )
"
BDEPEND="
	opencl? ( ${PYTHON_DEPS} )
	vulkan? ( media-libs/shaderc )
"

llama_cpp_normalize_cuda_arch_entry() {
	local entry="${1//[[:space:]]/}"
	local suffix="-real"

	[[ -n ${entry} ]] || return 0

	case ${entry} in
		all|all-major)
			printf '%s\n' "${entry}"
			return
			;;
		*-real|*-virtual)
			if [[ ${entry} =~ ^(12[0-9])(-(real|virtual))$ ]]; then
				printf '%sa%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
			else
				printf '%s\n' "${entry}"
			fi
			return
			;;
		sm_*)
			entry="${entry#sm_}"
			;;
		compute_*)
			entry="${entry#compute_}"
			suffix="-virtual"
			;;
	esac

	if [[ ${entry} == *.* ]]; then
		entry="${entry/./}"
	fi

	# Promote Blackwell consumer SM 12x to 12xa for architecture-accelerated FP4 MMA.
	if [[ ${entry} =~ ^12[0-9]$ ]]; then
		entry="${entry}a"
	fi

	if [[ ${entry} =~ ^[0-9]+[af]?$ ]]; then
		printf '%s%s\n' "${entry}" "${suffix}"
	else
		die "Invalid CUDA architecture entry: ${1}"
	fi
}

llama_cpp_normalize_cuda_arches() {
	local input="${1//,/;}"
	input="${input// /;}"
	input="${input//$'\n'/;}"
	input="${input//$'\r'/;}"

	local -a arch_list=()
	local entry arch
	local IFS=";"

	for entry in ${input}; do
		arch="$(llama_cpp_normalize_cuda_arch_entry "${entry}")"
		[[ -n ${arch} ]] || continue
		if ! has "${arch}" "${arch_list[@]}"; then
			arch_list+=( "${arch}" )
		fi
	done

	[[ ${#arch_list[@]} -gt 0 ]] || die "CUDA architecture list was empty (input: '${1}')"
	IFS=";"
	printf '%s\n' "${arch_list[*]}"
}

llama_cpp_portable_cuda_arches() {
	local cuda_ver
	cuda_ver="$(cuda_toolkit_version)"
	local -a arches=()

	if [[ -n ${cuda_ver} ]] && ver_test "${cuda_ver}" -lt 13; then
		arches+=( 50-virtual 61-virtual 70-virtual )
	fi

	arches+=( 75-virtual 80-virtual 86-real )

	if [[ -z ${cuda_ver} ]] || ver_test "${cuda_ver}" -ge 11.8; then
		arches+=( 89-real 90-virtual )
	fi

	if [[ -z ${cuda_ver} ]] || ver_test "${cuda_ver}" -ge 12.8; then
		arches+=( 120a-real )
	fi

	if [[ -z ${cuda_ver} ]] || ver_test "${cuda_ver}" -ge 12.9; then
		arches+=( 103-real 121a-real )
	fi

	local IFS=";"
	printf '%s\n' "${arches[*]}"
}

llama_cpp_procfs_model_to_cc() {
	local model="${1}"
	case ${model} in
		*GB10*|*"DGX Spark"*)
			printf '12.1\n' ;;
		*"RTX 50"[0-9][0-9]*|*"RTX PRO "*"Blackwell"*|*Blackwell*RTX*)
			printf '12.0\n' ;;
		*GB300*|*B300*)
			printf '10.3\n' ;;
		*GB200*|*B200*|*B100*)
			printf '10.0\n' ;;
		*GH200*|*H200*|*H100*|*H800*)
			printf '9.0\n' ;;
		*"RTX 40"[0-9][0-9]*|*"RTX "*"Ada"*|*" L40"*|*" L4"*|*" L2"*)
			printf '8.9\n' ;;
		*" A100"*|*" A800"*|*" A30"*)
			printf '8.0\n' ;;
		*"RTX 30"[0-9][0-9]*|*"RTX A"[0-9]*|*" A40"*|*" A16"*|*" A10"*|*" A2"*)
			printf '8.6\n' ;;
		*"RTX 20"[0-9][0-9]*|*"GTX 16"[0-9][0-9]*|*" T4"*|*"Quadro RTX"*|*"TITAN RTX"*)
			printf '7.5\n' ;;
		*" V100"*|*"TITAN V"*|*"GV100"*)
			printf '7.0\n' ;;
		*"GTX 10"[0-9][0-9]*|*"TITAN Xp"*|*" P40"*|*" P4"*|*" P6"*)
			printf '6.1\n' ;;
		*" P100"*)
			printf '6.0\n' ;;
		*)
			return 1 ;;
	esac
}

# Detect local NVIDIA GPU compute capability without capturing elog output.
# Stores normalized result in _LLAMA_CPP_DETECTED_CUDA_ARCHES.
llama_cpp_detect_cuda_arches() {
	_LLAMA_CPP_DETECTED_CUDA_ARCHES=""

	# Tier 1: nvidia-smi (also warms /dev/nvidia-uvm and /dev/nvidia-modeset)
	if command -v nvidia-smi >/dev/null 2>&1; then
		local smi_out line gpu_name cap
		local -a caps=()

		if smi_out="$(nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader,nounits 2>/dev/null)" &&
			[[ -n ${smi_out} ]]; then
			while IFS= read -r line; do
				[[ -n ${line} ]] || continue
				gpu_name="${line%,*}"
				cap="${line##*,}"
				cap="${cap//[[:space:]]/}"
				if [[ ${cap} =~ ^[0-9]+\.[0-9]+$ ]]; then
					einfo "Detected NVIDIA GPU (nvidia-smi): ${gpu_name} (compute_cap ${cap})"
					caps+=( "${cap}" )
				fi
			done <<< "${smi_out}"

			if [[ ${#caps[@]} -gt 0 ]]; then
				local IFS=";"
				_LLAMA_CPP_DETECTED_CUDA_ARCHES="$(llama_cpp_normalize_cuda_arches "${caps[*]}")"
				return 0
			fi
		fi
	fi

	# Tier 2: CUDA Toolkit's __nvcc_device_query
	local dev_query=""
	if [[ -x "${EPREFIX}/opt/cuda/bin/__nvcc_device_query" ]]; then
		dev_query="${EPREFIX}/opt/cuda/bin/__nvcc_device_query"
	elif command -v __nvcc_device_query >/dev/null 2>&1; then
		dev_query="$(command -v __nvcc_device_query)"
	fi

	if [[ -n ${dev_query} ]]; then
		local query_out
		if query_out="$("${dev_query}" 2>/dev/null)" && [[ ${query_out} =~ ^[0-9[:space:]]+$ ]]; then
			einfo "Detected NVIDIA GPU compute capability (__nvcc_device_query): ${query_out}"
			_LLAMA_CPP_DETECTED_CUDA_ARCHES="$(llama_cpp_normalize_cuda_arches "${query_out}")"
			return 0
		fi
	fi

	# Tier 3: World-readable /proc/driver/nvidia/gpus/*/information fallback
	local info_file model cc
	local -a proc_caps=()
	for info_file in /proc/driver/nvidia/gpus/*/information; do
		[[ -r ${info_file} ]] || continue
		model="$(sed -n 's/^Model:[[:space:]]*//p' "${info_file}")"
		[[ -n ${model} ]] || continue
		if cc="$(llama_cpp_procfs_model_to_cc "${model}")"; then
			einfo "Detected NVIDIA GPU (procfs fallback): ${model} -> compute_cap ${cc}"
			proc_caps+=( "${cc}" )
		else
			ewarn "Unrecognized NVIDIA GPU model in ${info_file}: ${model}"
		fi
	done

	if [[ ${#proc_caps[@]} -gt 0 ]]; then
		local IFS=";"
		_LLAMA_CPP_DETECTED_CUDA_ARCHES="$(llama_cpp_normalize_cuda_arches "${proc_caps[*]}")"
		return 0
	fi

	return 1
}

pkg_pretend() {
	[[ ${MERGE_TYPE} != binary ]] && use openmp && tc-check-openmp
}

pkg_setup() {
	[[ ${MERGE_TYPE} != binary ]] && use openmp && tc-check-openmp

	if use opencl; then
		python-any-r1_pkg_setup
	fi

	if use rocm; then
		linux-info_pkg_setup
		if linux-info_get_any_version && linux_config_exists; then
			if ! linux_chkconfig_present HSA_AMD_SVM; then
				ewarn "To use ROCm/HIP, enable HSA_AMD_SVM in your kernel."
			fi
		fi
	fi

	# Probe NVIDIA GPUs here in pkg_setup (runs as root, outside build sandbox,
	# where /dev/nvidia* permissions and NVML queries succeed) and persist the
	# detected architectures to src_configure via Portage's phase environment.
	if [[ ${MERGE_TYPE} != binary ]] && use cuda; then
		local requested="${LLAMA_CPP_CUDA_ARCHITECTURES:-${CUDAARCHS:-}}"
		_LLAMA_CPP_CUDA_ARCHES=""
		_LLAMA_CPP_CUDA_EXPLICIT_AUTO=""
		_LLAMA_CPP_CUDA_BUILDONLY=""
		[[ ${MERGE_TYPE} == buildonly ]] && _LLAMA_CPP_CUDA_BUILDONLY=1

		if [[ ${requested} == auto || ${requested} == native ]]; then
			_LLAMA_CPP_CUDA_EXPLICIT_AUTO=1
			if llama_cpp_detect_cuda_arches; then
				_LLAMA_CPP_CUDA_ARCHES="${_LLAMA_CPP_DETECTED_CUDA_ARCHES}"
			fi
		elif [[ -n ${requested} ]]; then
			_LLAMA_CPP_CUDA_ARCHES="$(llama_cpp_normalize_cuda_arches "${requested}")"
			einfo "Using user-specified CUDA architectures: ${_LLAMA_CPP_CUDA_ARCHES}"
		elif use native && [[ -z ${_LLAMA_CPP_CUDA_BUILDONLY} ]]; then
			if llama_cpp_detect_cuda_arches; then
				_LLAMA_CPP_CUDA_ARCHES="${_LLAMA_CPP_DETECTED_CUDA_ARCHES}"
			else
				ewarn "Could not auto-detect local NVIDIA GPU in pkg_setup; falling back to portable CUDA architectures."
				ewarn "Set LLAMA_CPP_CUDA_ARCHITECTURES or CUDAARCHS (e.g. \"12.0\") for a single-GPU native build."
			fi
		fi
	fi
}

src_unpack() {
	if [[ ${PV} == *9999* ]]; then
		git-r3_src_unpack
	else
		default
	fi

	if use webui; then
		rm -rf "${S}/ui/apps/chat/dist" || die
		if [[ ${PV} == *9999* ]]; then
			mkdir -p "${S}/ui/apps/chat/dist" || die
			einfo "Downloading webui dist from huggingface bucket..."
			wget -qO - "https://huggingface.co/buckets/ggml-org/llama-ui/resolve/latest/dist.tar.gz" \
				| tar -xzf - -C "${S}/ui/apps/chat/dist" || die
		else
			mv "${WORKDIR}/llama-${MY_PV}" "${S}/ui/apps/chat/dist" || die
		fi
	fi
}

src_prepare() {
	use cuda && tc-is-gcc && cuda_src_prepare
	cmake_src_prepare

	if use examples; then
		mkdir -p "${BUILD_DIR}/tinyllamas" || die
		cp "${DISTDIR}/ggml-org_models_tinyllamas_stories15M-q4_0-${TINY_LLAMAS_COMMIT}.gguf" \
			"${BUILD_DIR}/tinyllamas/stories15M-q4_0.gguf" || die
	fi
}

src_configure() {
	local CMAKE_BUILD_TYPE="Release"

	if [[ ${PV} == *9999* ]]; then
		local mycmakeargs=(
			-DLLAMA_BUILD_NUMBER="$(git rev-list --count HEAD)"
			-DLLAMA_BUILD_COMMIT="$(git rev-parse HEAD)"
		)
	else
		local mycmakeargs=( -DLLAMA_BUILD_NUMBER="${MY_PV#b}" )
	fi

	mycmakeargs+=(
		-DCMAKE_C_FLAGS_RELEASE="-DNDEBUG"
		-DCMAKE_CXX_FLAGS_RELEASE="-DNDEBUG"
		-DCMAKE_SKIP_BUILD_RPATH=ON
		-DGGML_BACKEND_DL=OFF
		-DGGML_CCACHE=OFF
		-DGGML_CPU_REPACK="$(usex cpu-repack)"
		-DGGML_LLAMAFILE=ON
		-DGGML_LTO="$(usex lto)"
		-DGGML_NATIVE="$(usex native)"
		-DLLAMA_BUILD_APP="$(usex server)"
		-DLLAMA_BUILD_EXAMPLES="$(usex examples)"
		-DLLAMA_BUILD_SERVER="$(usex server)"
		-DLLAMA_BUILD_TESTS=OFF
		# Offline WebUI embedding: scripts/ui-assets.cmake always prefers
		# ui/apps/chat/dist/index.html (staged in src_unpack when USE=webui)
		# before checking LLAMA_BUILD_UI (npm) or LLAMA_USE_PREBUILT_UI (HF download).
		-DLLAMA_BUILD_UI=OFF
		-DLLAMA_USE_PREBUILT_UI=OFF

		-DGGML_CUDA="$(usex cuda)"
		-DGGML_OPENCL="$(usex opencl)"
		-DGGML_OPENMP="$(usex openmp)"
		-DGGML_RPC="$(usex rpc)"
		-DGGML_VULKAN="$(usex vulkan)"
	)

	if ! use native; then
		mycmakeargs+=(
			-DGGML_SSE42="$(usex cpu_flags_x86_sse4_2)"
			-DGGML_AVX="$(usex cpu_flags_x86_avx)"
			-DGGML_AVX_VNNI="$(usex cpu_flags_x86_avx_vnni)"
			-DGGML_AVX2="$(usex cpu_flags_x86_avx2)"
			-DGGML_BMI2="$(usex cpu_flags_x86_bmi2)"
			-DGGML_AVX512_VBMI="$(usex cpu_flags_x86_avx512vbmi)"
			-DGGML_AVX512_VNNI="$(usex cpu_flags_x86_avx512_vnni)"
			-DGGML_AVX512_BF16="$(usex cpu_flags_x86_avx512_bf16)"
			-DGGML_FMA="$(usex cpu_flags_x86_fma3)"
			-DGGML_F16C="$(usex cpu_flags_x86_f16c)"
			-DGGML_AMX_TILE="$(usex cpu_flags_x86_amx_tile)"
			-DGGML_AMX_INT8="$(usex cpu_flags_x86_amx_int8)"
			-DGGML_AMX_BF16="$(usex cpu_flags_x86_amx_bf16)"
		)

		if use cpu_flags_x86_avx512f &&
			use cpu_flags_x86_avx512cd &&
			use cpu_flags_x86_avx512vl &&
			use cpu_flags_x86_avx512dq &&
			use cpu_flags_x86_avx512bw; then
			mycmakeargs+=( -DGGML_AVX512=ON )
		else
			mycmakeargs+=( -DGGML_AVX512=OFF )
		fi
	fi

	if use openblas; then
		mycmakeargs+=(
			-DGGML_BLAS=ON
			-DGGML_BLAS_VENDOR=OpenBLAS
		)
	elif use blis; then
		mycmakeargs+=(
			-DGGML_BLAS=ON
			-DGGML_BLAS_VENDOR=FLAME
		)
	elif use flexiblas; then
		mycmakeargs+=(
			-DGGML_BLAS=ON
			-DGGML_BLAS_VENDOR=FlexiBLAS
		)
	fi

	if use cuda; then
		if tc-is-gcc; then
			local -x CUDAHOSTCXX="$(cuda_gccdir)/g++"
		fi

		cuda_add_sandbox -w
		addpredict "/dev/char/"

		local cuda_arches="${_LLAMA_CPP_CUDA_ARCHES}"
		if [[ -z ${cuda_arches} ]]; then
			# Fallback retry if pkg_setup was bypassed (e.g. direct `ebuild ... configure`)
			local requested="${LLAMA_CPP_CUDA_ARCHITECTURES:-${CUDAARCHS:-}}"
			if [[ ${requested} == auto || ${requested} == native ]]; then
				_LLAMA_CPP_CUDA_EXPLICIT_AUTO=1
				llama_cpp_detect_cuda_arches && cuda_arches="${_LLAMA_CPP_DETECTED_CUDA_ARCHES}"
			elif [[ -n ${requested} ]]; then
				cuda_arches="$(llama_cpp_normalize_cuda_arches "${requested}")"
			elif use native && [[ -z ${_LLAMA_CPP_CUDA_BUILDONLY} ]]; then
				llama_cpp_detect_cuda_arches && cuda_arches="${_LLAMA_CPP_DETECTED_CUDA_ARCHES}"
			fi
		fi

		if [[ -z ${cuda_arches} ]]; then
			if [[ -n ${_LLAMA_CPP_CUDA_EXPLICIT_AUTO} ]]; then
				die "Explicit CUDA auto-detection failed; set LLAMA_CPP_CUDA_ARCHITECTURES or CUDAARCHS (e.g. \"12.0\")"
			fi
			# Prevent upstream from setting CMAKE_CUDA_ARCHITECTURES=native when
			# USE=native is enabled on a headless/binpkg build host without GPU access.
			cuda_arches="$(llama_cpp_portable_cuda_arches)"
		fi

		local cuda_compress="${LLAMA_CPP_CUDA_COMPRESSION_MODE}"
		if [[ -z ${cuda_compress} ]]; then
			cuda_compress="$(usex native speed size)"
		fi

		einfo "CUDA configuration: CMAKE_CUDA_ARCHITECTURES=${cuda_arches}"
		einfo "CUDA configuration: GGML_CUDA_FA_QUANTS=${LLAMA_CPP_CUDA_FA_QUANTS}, GGML_CUDA_COMPRESSION_MODE=${cuda_compress}"

		mycmakeargs+=(
			-DCMAKE_CUDA_ARCHITECTURES="${cuda_arches}"
			-DCMAKE_CUDA_FLAGS_RELEASE="-O3 -DNDEBUG"
			-DGGML_CUDA_COMPRESSION_MODE="${cuda_compress}"
			-DGGML_CUDA_FA=ON
			-DGGML_CUDA_FA_QUANTS="${LLAMA_CPP_CUDA_FA_QUANTS}"
			-DGGML_CUDA_GRAPHS=ON
			-DGGML_CUDA_NO_PEER_COPY=OFF
			-DGGML_CUDA_NO_VMM=OFF
		)
	fi

	if use rocm; then
		export HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)"
		mycmakeargs+=(
			-DAMDGPU_TARGETS="$(get_amdgpu_flags)"
			-DGGML_HIP=ON
			-DGGML_HIP_GRAPHS=ON
			-DGGML_HIP_ROCWMMA_FATTN="$(usex wmma)"
		)
	fi

	cmake_src_configure
}
