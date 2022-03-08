rm core.*
DEVICE_NUM_PER_NODE=${1-1}
dataset_type=${2-kaggle}
embedding_type=${3-OneEmbedding}
cache_type=${4-device_host} # "device_host"

BATHSIZE=8192
emb_size=16
export EMBEDDING_SIZE=$emb_size

cmd=""
cmd+="--max_iter 1000 "
cmd+="--loss_print_every_n_iter 100 "
cmd+="--eval_interval 100 "
cmd+="--learning_rate 2 "
cmd+="--model_save_dir ckpt "
cmd+="--model_load_dir /minio/sde/model_zoo/dlrm_baseline_params_emb$emb_size "
# cmd+="--use_fp16 "
# cmd+="--loss_scale_policy static "

# dataset 
if [ "$dataset_type" = "criteo1t" ]; then
    DATA_DIR=/minio/sdd/dataset/criteo1t/add_slot_size_snappy_true
    column_size_array='227605432,39060,17295,7424,20265,3,7122,1543,63,130229467,3067956,405282,10,2209,11938,155,4,976,14,292775614,40790948,187188510,590152,12973,108,36'
    eval_batch_size=262144
    eval_batchs=$(( 89137319 / eval_batch_size ))
    eval_batchs=10
else
    # test: 3274330, val: 3274328, train: 39291958
    DATA_DIR=/tank/dataset/criteo_kaggle/dlrm_hugectr_parquet
    column_size_array='1460,583,10131227,2202608,305,24,12517,633,3,93145,5683,8351593,3194,27,14992,5461306,10,5652,2173,4,7046547,18,15,286181,105,142572'
    eval_batch_size=81858
    eval_batchs=$(( 3274330 / eval_batch_size ))
    cmd+="--train_sub_folders train "
    cmd+="--val_sub_folders val "
fi
cmd+="--data_dir $DATA_DIR "
cmd+="--column_size_array $column_size_array "
cmd+="--batch_size $BATHSIZE "
cmd+="--eval_batchs $eval_batchs "
cmd+="--eval_batch_size $eval_batch_size "

# DLRM
## MLP
cmd+="--mlp_type MLP "
cmd+="--bottom_mlp 512,256,$emb_size "
cmd+="--top_mlp 1024,1024,512,256 "

## Embedding
cmd+="--embedding_vec_size $emb_size "
cmd+="--embedding_type $embedding_type "
if [ "$embedding_type" = "Embedding" ]; then
    cmd+="--embedding_split_axis 1 "
    test_case="train_${embedding_type}_${dataset_type}_g${DEVICE_NUM_PER_NODE}"
elif [ "$embedding_type" = "OneEmbedding" ]; then
    test_case="train_${embedding_type}_${cache_type}_${dataset_type}_g${DEVICE_NUM_PER_NODE}"
    block_based_dir=/minio/sde/model_zoo/dlrm_baseline_params_emb$emb_size
    # cp $block_based_dir/one_embedding0/index_cp $block_based_dir/one_embedding0/index
    if [ "$cache_type" = "device_ssd" ]; then # gpu + ssd
        cache_memory_budget_mb="16384"
    elif [ "$cache_type" = "device_only" ]; then # gpu only, cache_memory > embedding table
        cache_memory_budget_mb="16384"
    elif [ "$cache_type" = "host_ssd" ]; then # cpu only, cache_memory > embedding table
        cache_memory_budget_mb="16384"
    elif [ "$cache_type" = "host_only" ]; then # cpu only, cache_memory > embedding table
        cache_memory_budget_mb="16384"
    elif [ "$cache_type" = "device_host" ]; then # gpu + cpu
        cache_memory_budget_mb="4096,16384"
    fi
    cmd+="--persistent_path $block_based_dir/one_embedding0 "
    cmd+="--cache_type $cache_type "
    cmd+="--cache_memory_budget_mb $cache_memory_budget_mb "
fi

cmd+="--test_name $test_case"
# echo $cmd

# export CUDA_VISIBLE_DEVICES=1
# export ONEFLOW_DEBUG_MODE=True
python3 -m oneflow.distributed.launch \
    --nproc_per_node $DEVICE_NUM_PER_NODE \
    --nnodes 1 \
    --node_rank 0 \
    --master_addr 127.0.0.1 \
    graph_train.py $cmd | tee ${test_case}.log