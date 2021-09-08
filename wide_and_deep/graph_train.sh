rm core.*

# export CUDA_VISIBLE_DEVICES=3

python3 graph_train.py \
    --model_save_dir ./checkpoints \
    --print_interval 100 \
    --deep_dropout_rate 0 \
    --max_iter 1000

