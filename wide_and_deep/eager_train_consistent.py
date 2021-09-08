import os
import time
import numpy as np
from sklearn.metrics import roc_auc_score
import oneflow as flow
from tqdm import tqdm
from config import get_args
from dataloader_utils_consistent import OFRecordDataLoader
from wide_and_deep_module import WideAndDeep
from util import dump_to_npy, save_param_npy

   
world_size = flow.env.get_world_size()
placement = flow.placement("cpu", {0: range(world_size)})

def prepare_modules(args):
    
    sbp = flow.sbp.split(0)
    train_dataloader = OFRecordDataLoader(
        args, data_root=args.data_dir, batch_size=args.batch_size,placement=placement,sbp=sbp
    )
    val_dataloader = OFRecordDataLoader(args, data_root=args.data_dir, mode="val",batch_size=args.batch_size,placement=placement,sbp=sbp)

    wdl_module = WideAndDeep(args)
    #model->consistent
    wdl_module = wdl_module.to_consistent(placement=placement, sbp=flow.sbp.broadcast)
    wdl_module = wdl_module.to("cuda")


    if args.model_load_dir != "":
        print("load checkpointed model from ", args.model_load_dir)
        wdl_module.load_state_dict(flow.load(args.model_load_dir))

    if args.save_initial_model and args.model_save_dir != "":
        path = os.path.join(args.model_save_dir, "initial_checkpoint")
        if not os.path.isdir(path):
            flow.save(wdl_module.state_dict(), path)

    bce_loss = flow.nn.BCELoss(reduction="mean")
    bce_loss.to("cuda")

    opt = flow.optim.SGD(wdl_module.parameters(), lr=args.learning_rate, momentum=0.9)
    return train_dataloader, val_dataloader, wdl_module, bce_loss, opt


def print_eval_metrics(step, loss, lables_list, predicts_list):
    all_labels = np.concatenate(lables_list, axis=0)
    all_predicts = np.concatenate(predicts_list, axis=0)
    auc = (
        "NaN"
        if np.isnan(all_predicts).any()
        else roc_auc_score(all_labels, all_predicts)
    )
    rank=flow.env.get_rank()
    print(f"device {rank}: iter {step} eval_loss {loss} auc {auc}")



if __name__ == "__main__":
    args = get_args()
    train_dataloader, val_dataloader, wdl_module, loss, opt = prepare_modules(args)

    losses = []
    wdl_module.train()
    for i in tqdm(range(args.max_iter)):
        (
            labels,
            dense_fields,
            wide_sparse_fields,
            deep_sparse_fields,
        ) = train_dataloader()
        ##都是split(0)的
        labels = labels.to("cuda").to(dtype=flow.float32)
        dense_fields = dense_fields.to("cuda")
        wide_sparse_fields = wide_sparse_fields.to("cuda")
        deep_sparse_fields = deep_sparse_fields.to("cuda")
        
        predicts = wdl_module(dense_fields, wide_sparse_fields, deep_sparse_fields)
        # NOTE(zwx): scale init grad with world_size
        # because consistent_tensor.mean() include dividor numel * world_size
        train_loss = loss(predicts, labels)
        #train_loss是partial_sum
        train_loss = train_loss / world_size
        #各个rank 打印local loss
        losses.append(train_loss.to_local().numpy().mean())
        train_loss.backward()
        for param_group in opt.param_groups:
                for param in param_group.parameters:
                    param.grad /= world_size
        #这里报错了
        opt.step()
        opt.zero_grad()
       

        if (i + 1) % args.print_interval == 0:
            l = sum(losses) / len(losses)
            losses = []
            print(f"iter {i+1} train_loss {l} time {time.time()}")
            if args.eval_batchs <= 0:
                continue

            eval_loss_acc = 0.0
            lables_list = []
            predicts_list = []
            wdl_module.eval()
            for j in range(args.eval_batchs):
                (
                    labels,
                    dense_fields,
                    wide_sparse_fields,
                    deep_sparse_fields,
                ) = val_dataloader()
                labels = labels.to("cuda").to(dtype=flow.float32)
                dense_fields = dense_fields.to("cuda")
                wide_sparse_fields = wide_sparse_fields.to("cuda")
                deep_sparse_fields = deep_sparse_fields.to("cuda")
                predicts = wdl_module(
                    dense_fields, wide_sparse_fields, deep_sparse_fields
                )
                eval_loss = loss(predicts, labels)
                eval_loss = eval_loss / world_size
                eval_loss_acc += eval_loss.to_local().numpy().mean()
                lables_list.append(labels.numpy())
                predicts_list.append(predicts.numpy())

            print_eval_metrics(
                i + 1, eval_loss_acc / args.eval_batchs, lables_list, predicts_list
            )
            wdl_module.train()
