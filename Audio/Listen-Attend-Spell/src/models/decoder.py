import oneflow as flow
import oneflow.nn as nn
import oneflow.nn.functional as F

from attention import DotProductAttention
from LSTM import CustomLSTM
from utils import IGNORE_ID, pad_list


class Decoder(nn.Module):
    """
    """

    def __init__(self, vocab_size, embedding_dim, sos_id, eos_id, hidden_size,
                 num_layers, bidirectional_encoder=True):
        super(Decoder, self).__init__()
        # Hyper parameters
        # embedding + output
        self.vocab_size = vocab_size
        self.embedding_dim = embedding_dim
        self.sos_id = sos_id  # Start of Sentence
        self.eos_id = eos_id  # End of Sentence
        # rnn
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.encoder_hidden_size = hidden_size  # must be equal now
        # Components
        self.embedding = nn.Embedding(self.vocab_size, self.embedding_dim)
        self.rnn = nn.ModuleList()
        self.rnn += [CustomLSTM(input_sz=(self.embedding_dim +
                                self.encoder_hidden_size),  hidden_sz=self.hidden_size)]
        for l in range(1, self.num_layers):
            self.rnn += [CustomLSTM(input_sz=self.hidden_size,
                                    hidden_sz=self.hidden_size)]
        self.attention = DotProductAttention()
        self.mlp = nn.Sequential(
            nn.Linear(self.encoder_hidden_size + self.hidden_size,
                      self.hidden_size),
            nn.Tanh(),
            nn.Linear(self.hidden_size, self.vocab_size))

    def zero_state(self, encoder_padded_outputs, H=None):
        N = encoder_padded_outputs.size(0)
        H = self.hidden_size if H == None else H
        return encoder_padded_outputs.new_ones((N, H)).fill_(0)

    def forward(self, padded_input, encoder_padded_outputs):
        """
        Args:
            padded_input: N x To
            # encoder_hidden: (num_layers * num_directions) x N x H
            encoder_padded_outputs: N x Ti x H

        Returns:
        """
        
        # *********Get Input and Output
        # from espnet/Decoder.forward()
        # TODO: need to make more smart way
        ys = [y[y != IGNORE_ID] for y in padded_input]  # parse padded ys
        # prepare input and output word sequences with sos/eos IDs
        eos = ys[0].new_ones([1]).fill_(self.eos_id)
        sos = ys[0].new_ones([1]).fill_(self.sos_id)
        ys_in = [flow.cat([sos, y], dim=0)
                 for y in ys]
        ys_out = [flow.cat([y, eos], dim=0) for y in ys] 
        # padding for ys with -1
        # pys: utt x olen
        ys_in_pad = pad_list(ys_in, self.eos_id)
        ys_out_pad = pad_list(ys_out, IGNORE_ID)
        assert ys_in_pad.size() == ys_out_pad.size()
        batch_size = ys_in_pad.size(0)
        output_length = ys_in_pad.size(1)

        # *********Init decoder rnn
        h_list = [self.zero_state(encoder_padded_outputs)]
        c_list = [self.zero_state(encoder_padded_outputs)]
        for l in range(1, self.num_layers):
            h_list.append(self.zero_state(encoder_padded_outputs))
            c_list.append(self.zero_state(encoder_padded_outputs))
        att_c = self.zero_state(encoder_padded_outputs,
                                H=encoder_padded_outputs.size(2))
        y_all = []

        # **********LAS: 1. decoder rnn 2. attention 3. concate and MLP
        embedded = self.embedding(ys_in_pad)
        for t in range(output_length): #22
            # step 1. decoder RNN: s_i = RNN(s_i−1,y_i−1,c_i−1)
            rnn_input = flow.cat((embedded[:, t, :], att_c), dim=1)
            hidden_seq,(h_list[0], c_list[0]) = self.rnn[0](
                rnn_input.unsqueeze(1), init_states=(h_list[0], c_list[0]))
            for l in range(1, self.num_layers):
                hidden_seq, (h_list[l], c_list[l]) = self.rnn[l](
                    h_list[l-1].unsqueeze(1), init_states=(h_list[l], c_list[l]))
            rnn_output = h_list[-1]
            # step 2. attention: c_i = AttentionContext(s_i,h)
            att_c, att_w = self.attention(rnn_output.unsqueeze(dim=1),
                                          encoder_padded_outputs)
            att_c = att_c.squeeze(dim=1)
            # step 3. concate s_i and c_i, and input to MLP
            mlp_input = flow.cat((rnn_output, att_c), dim=1)
            predicted_y_t = self.mlp(mlp_input)
            y_all.append(predicted_y_t)

        y_all = flow.stack(y_all, dim=1)  # N x To x C
        # **********Cross Entropy Loss
        y_all = y_all.view(batch_size * output_length, self.vocab_size)
        ce_loss_fn = nn.CrossEntropyLoss(
            ignore_index=IGNORE_ID, reduction='mean').to(ys_out_pad.device)
        ce_loss = ce_loss_fn(y_all, ys_out_pad.view(-1))
        # TODO: should minus 1 here ?
        return ce_loss


    def recognize_beam(self, encoder_outputs, char_list, args):
        """Beam search, decode one utterence now.
        Args:
            encoder_outputs: T x H torch.Size([ 426,512])
            char_list: list of character
            args: args.beam

        Returns:
            nbest_hyps:
        """
        # search params
        beam = args.beam_size
        nbest = args.nbest
        if args.decode_max_len == 0:
            maxlen = encoder_outputs.size(0)
        else:
            maxlen = args.decode_max_len

        # *********Init decoder rnn
        h_list = [self.zero_state(encoder_outputs.unsqueeze(0))]
        c_list = [self.zero_state(encoder_outputs.unsqueeze(0))]
        for l in range(1, self.num_layers):
            h_list.append(self.zero_state(encoder_outputs.unsqueeze(0)))
            c_list.append(self.zero_state(encoder_outputs.unsqueeze(0)))
        att_c = self.zero_state(encoder_outputs.unsqueeze(0),
                                H=encoder_outputs.unsqueeze(0).size(2))
        # prepare sos
        y = self.sos_id
        vy = flow.ones(1).fill_(0).type_as(encoder_outputs).long()
        
        hyp = {'score': 0.0, 'yseq': [y], 'c_prev': c_list, 'h_prev': h_list,
               'a_prev': att_c}
        hyps = [hyp]
        ended_hyps = []

        for i in range(maxlen):
            hyps_best_kept = []
            for hyp in hyps:
                vy[0] = hyp['yseq'][i]
                vy = vy.to(device=encoder_outputs.device)
                embedded = self.embedding(vy)
                rnn_input = flow.cat((embedded, hyp['a_prev']), dim=1)
                hidden_seq, (h_list[0], c_list[0]) = self.rnn[0](
                    rnn_input.unsqueeze(1), init_states=(hyp['h_prev'][0], hyp['c_prev'][0]))
                for l in range(1, self.num_layers):
                    hidden_seq, (h_list[l], c_list[l])=self.rnn[l](
                        h_list[l-1].unsqueeze(1), init_states=(hyp['h_prev'][l], hyp['c_prev'][l]))
                rnn_output = h_list[-1]
                # step 2. attention: c_i = AttentionContext(s_i,h)
                # below unsqueeze: (N x H) -> (N x 1 x H)
                att_c, att_w = self.attention(rnn_output.unsqueeze(dim=1),
                                              encoder_outputs.unsqueeze(0))
                att_c = att_c.squeeze(dim=1)
                # step 3. concate s_i and c_i, and input to MLP
                mlp_input = flow.cat((rnn_output, att_c), dim=1)
                predicted_y_t = self.mlp(mlp_input)
                local_logit = F.softmax(predicted_y_t)
                local_scores = flow.log(local_logit)
                # topk scores
                local_best_scores, local_best_ids = flow.topk(
                    local_scores, beam, dim=1)

                for j in range(beam):
                    new_hyp = {}
                    new_hyp['h_prev'] = h_list[:]
                    new_hyp['c_prev'] = c_list[:]
                    new_hyp['a_prev'] = att_c[:]
                    new_hyp['score'] = hyp['score'] + local_best_scores[0, j]
                    new_hyp['yseq'] = [0] * (1 + len(hyp['yseq']))
                    new_hyp['yseq'][:len(hyp['yseq'])] = hyp['yseq']
                    new_hyp['yseq'][len(hyp['yseq'])] = int(float(local_best_ids[0, j].numpy()))
                    # will be (2 x beam) hyps at most
                    hyps_best_kept.append(new_hyp)

                hyps_best_kept = sorted(hyps_best_kept,
                                        key=lambda x: x['score'],
                                        reverse=True)[:beam]
            # end for hyp in hyps
            hyps = hyps_best_kept

            # add eos in the final loop to avoid that there are no ended hyps
            if i == maxlen - 1:
                for hyp in hyps:
                    hyp['yseq'].append(self.eos_id)

            # add ended hypothes to a final list, and removed them from current hypothes
            # (this will be a probmlem, number of hyps < beam)
            remained_hyps = []
            for hyp in hyps:
                if hyp['yseq'][-1] == self.eos_id:
                    ended_hyps.append(hyp)
                else:
                    remained_hyps.append(hyp)

            hyps = remained_hyps
            if len(hyps) > 0:
                print('remeined hypothes: ' + str(len(hyps)))
            else:
                print('no hypothesis. Finish decoding.')
                break

            for hyp in hyps:
                print('hypo: ' + ''.join([char_list[int(x)]
                                          for x in hyp['yseq'][1:]]))
        # end for i in range(maxlen)
        nbest_hyps = sorted(ended_hyps, key=lambda x: x['score'], reverse=True)[
            :min(len(ended_hyps), nbest)]
        return nbest_hyps
