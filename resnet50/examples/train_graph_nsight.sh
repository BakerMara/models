# set -aux
# export PYTHONPATH=/home/luyang/Oneflow/oneflow/python:$PYTHONPATH
# bash examples/train_graph_nsight.sh > nsight_graph_resnet50_fp32_1n1g_bz96_20iter_cpu_decoder@35edcc50e.log
DEVICE_NUM_PER_NODE=1
MASTER_ADDR=127.0.0.1
NUM_NODES=1
NODE_RANK=0

export PYTHONUNBUFFERED=1
echo PYTHONUNBUFFERED=$PYTHONUNBUFFERED
export NCCL_LAUNCH_MODE=PARALLEL
echo NCCL_LAUNCH_MODE=$NCCL_LAUNCH_MODE
# export NCCL_DEBUG=INFO

CHECKPOINT_SAVE_PATH="./graph_checkpoints"
if [ ! -d "$CHECKPOINT_SAVE_PATH" ]; then
    mkdir $CHECKPOINT_SAVE_PATH
fi

CHECKPOINT_LOAD_PATH="./init_ckpt_by_lazy"

OFRECORD_PATH="/dataset/ImageNet/ofrecord"
# OFRECORD_PATH="/datasets/imagenet/ofrecord"
OFRECORD_PART_NUM=256
LEARNING_RATE=0.096
MOM=0.875
EPOCH=50
TRAIN_BATCH_SIZE=96
VAL_BATCH_SIZE=50

# SRC_DIR=/path/to/models/resnet50
SRC_DIR=$(realpath $(dirname $0)/..)

/home/luyang/nsight-systems-2021.2.1/bin/nsys profile -o nsight_graph_resnet50_fp32_1n1g_bz96_20iter_cpu_decoder@35edcc50e.qdrep \
python3 $SRC_DIR/train_nsight_profile.py \
        --save $CHECKPOINT_SAVE_PATH \
        --ofrecord-path $OFRECORD_PATH \
        --ofrecord-part-num $OFRECORD_PART_NUM \
        --num-devices-per-node $DEVICE_NUM_PER_NODE \
        --lr $LEARNING_RATE \
        --momentum $MOM \
        --num-epochs $EPOCH \
        --train-batch-size $TRAIN_BATCH_SIZE \
        --val-batch-size $VAL_BATCH_SIZE \
        --graph \
        --print-interval 1 \
        # --use-gpu-decode \
        # --metric-local True \
        # --metric-one-rank 0
        # --metric-train-acc False
